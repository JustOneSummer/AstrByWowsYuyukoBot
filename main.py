"""战舰世界战绩查询插件（AstrBot 接入层 / 唯一入口）。

AstrBot 通过 `main.py` 发现插件：它按 `data.plugins.<插件目录>.main` 这个模块路径
去匹配指令处理器，所以**带 `@filter.xxx` 装饰器的方法必须留在本文件**，
否则处理器会因为模块路径对不上而不被发现。

具体实现都在同级的 `yuyuko_bot/` 包里：

- `yuyuko_bot/__init__.py`  内置 hikari-core 的 sys.path 注入
- `yuyuko_bot/config.py`    AstrBot 配置 -> hikari-core 配置的映射
- `yuyuko_bot/runtime.py`   hikari-core 的启动 / 就绪判断 / 收尾
- `yuyuko_bot/selection.py` 「等用户回复序号」的选择流程
- `yuyuko_bot/sender.py`    渲染结果的落盘与发送
"""

import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
# GreedyStr 是 AstrBot 的「贪婪参数」标记（AstrBotDevs/AstrBot#1759 合入）：
# 命令处理器声明 `args: GreedyStr` 后，CommandFilter 会把「指令名之后的全部剩余文本」
# 原样交过来，顺带完成唤醒前缀剥离与连续空白归一化。它没有在 astrbot.api 里再导出，
# 所以从实现模块直接引入（注意必须放在 astrbot.api 之后，否则会踩到循环导入）。
from astrbot.core.star.filter.command import GreedyStr

# 用**相对导入**取插件内部实现：AstrBot 以 `data.plugins.<插件目录>.main` 的形式导入
# 本文件，本文件因此属于该包，相对导入稳定可用；而写成绝对导入 `import yuyuko_bot`
# 会依赖「插件目录在 sys.path 上」，一旦插件目录被加入 sys.path 还可能解析到
# 插件目录之外的另一个同名目录，反而更脆。
# 注意：`hikari_core` 的 sys.path 注入在 `yuyuko_bot/__init__.py` 里，
# 上面这行相对导入之后，`from hikari_core import ...` 才可用。
from .yuyuko_bot import (  # noqa: E402
    CoreRuntime,
    OutputSender,
    SelectionManager,
    install_logging_bridge,
    reply_text,
)
from hikari_core import Hikari_Model, callback_hikari, init_hikari  # noqa: E402

COMMAND = "wws"
# AstrBot 平台适配器 -> hikari 的 platform 取值
PLATFORM_MAP = {
    "aiocqhttp": "QQ",
    "qq_official": "QQ_OFFICIAL",
}


class WowsYuyuko(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.runtime = CoreRuntime(config)
        self.selection = SelectionManager()
        self.sender = OutputSender()
        # hikari-core 的截图服务是**单例单浏览器**（minimal_screens_hot_service），
        # 而 AstrBot 对每条消息都 `asyncio.create_task(...)`（core/event_bus.py），
        # 并发请求会在 `get_instance()` 首次惰性初始化上打架 —— 并发执行
        # `start()` 会重复拉起 playwright / 浏览器进程（内存暴涨）。
        # 这里串行化「指令处理 + 渲染」，与 HikariBot-Official 单实例渲染的语义一致。
        self._render_lock = asyncio.Semaphore(1)

    async def initialize(self):
        """插件激活：下发配置并启动 hikari-core（对应 HikariBot-Official 的 start.py）。"""
        # 热重载可能不重跑包内模块级调用，这里再确保一次（幂等）
        install_logging_bridge()
        await self.runtime.start()

    async def terminate(self):
        """插件被禁用/重载时清理临时资源。"""
        try:
            logger.info("开始执行wows-yuyuko插件临时资源销毁")
            # 先掐掉还在落盘/发送的后台任务，免得刚删完目录它们又往里写
            await self.sender.cancel_pending()
            self.selection.clear()
            await self.runtime.shutdown()
            logger.info("wows-yuyuko插件临时资源销毁完成")
        except Exception as e:
            logger.error(f"清理wows-yuyuko插件临时资源失败: {e}")

    @filter.command(COMMAND)
    async def wws(self, event: AstrMessageEvent, args: GreedyStr):
        """战舰世界战绩查询，如 /wws recent me

        args 由 AstrBot 的 CommandFilter 负责解析：唤醒前缀与指令名都已被剥掉、
        连续空白已归一化，没带参数时就是空字符串 —— 插件侧不需要自己拆前缀。
        """
        platform = PLATFORM_MAP.get(event.get_platform_name())
        if platform is None:
            await reply_text(event, f"不支持的平台消息 name={event.get_platform_name()}")
            return

        if not self.runtime.is_ready():
            # 首次启动要下模板/资源，这期间别让指令静默卡死
            await reply_text(event, "正在准备渲染资源，请稍等一会儿再查询哦~")
            return

        session = event.unified_msg_origin
        if self.selection.is_pending(session):
            # 已有一次等待中的选择：这时再发 wws 会挤掉它的状态槽，让前一次白等到超时
            await reply_text(event, "上一条查询还在等你回复序号，请先选完或等待超时~")
            return

        async with self._render_lock:
            try:
                hikari = await init_hikari(
                    command_text=args,
                    platform=platform,
                    PlatformId=event.get_sender_id(),
                    BotId=event.get_self_id(),
                    GroupId=event.get_group_id(),
                )
                if hikari.Status == 'wait':
                    # 先登记状态槽再发选择界面，免得用户手快先回序号时状态还没就绪；
                    # 发送/等待的顺序仍然是「发图 → 等回复」。
                    state = self.selection.register(session, hikari.Input.Select_Data)
                    await self.sender.send(event, hikari)
                    index = await self.selection.wait(session, state)
                    if index is None:
                        hikari = hikari.error('已超时退出')
                    else:
                        hikari.Input.Select_Index = index
                        hikari = await callback_hikari(hikari)
                await self.sender.send(event, hikari)
            except Exception as e:
                logger.exception(f"指令处理异常 {e}")
                await reply_text(event, "呜呜呜发生了错误，可能是网络问题，如果过段时间不能恢复请联系麻麻哦~")

    @filter.regex(r"^\d+$")
    async def change_select_state(self, event: AstrMessageEvent):
        """消费选择流程中用户回复的序号。

        只匹配「纯数字」消息：不必对每条消息都挂一个全局监听（那会让 AstrBot 把每条
        消息都当成"已唤醒"，干扰后续阶段），也天然排除了用户顺手发别的指令被误当成选择。
        """

        async def reply(text: str) -> None:
            await reply_text(event, text)

        consumed = await self.selection.consume_reply(
            event.unified_msg_origin, event.message_str, reply
        )
        if consumed:
            # 这条消息已经被选择流程消费掉了，别再往后续阶段（LLM 等）流
            event.stop_event()
