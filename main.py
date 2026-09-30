"""战舰世界战绩查询插件（AstrBot 接入层 / 唯一入口）。

AstrBot 通过 `main.py` 发现插件：它按 `data.plugins.<插件目录>.main` 这个模块路径
去匹配处理器，所以**带 `@filter.xxx` 和 `@llm_tool` 装饰的方法必须留在本文件**，
否则会因为模块路径对不上而不被发现（见 `astrbot/core/star/star_manager.py`：
`ft.handler.__module__ == metadata.module_path` 才会绑定 Star 实例）。

具体实现拆在同级两个包里：

- `yuyuko_bot/`  AstrBot 插件骨架：配置映射、hikari 生命周期、选择流程、结果发送
- `yuyuko_llm/`  面向 LLM 的工具层：字段提取、查询编排、工具描述、开关与限流

本文件因此只保留三类东西：插件类与生命周期、指令处理器、LLM 工具处理器。
"""

import asyncio
import functools

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
# llm_tool：把一个 async 函数注册成 LLM 可调用的工具（function-calling）。
# 它从 astrbot.api 顶层导出；工具的参数 schema 由函数 docstring 的 Args: 段生成。
from astrbot.api import llm_tool
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
# 所以必须**先**导入 yuyuko_bot，`yuyuko_llm` 内部的 hikari_core 导入才可用。
from .yuyuko_bot import (
    CoreRuntime,
    OutputSender,
    SelectionManager,
    install_logging_bridge,
    reply_text,
)
from hikari_core import Hikari_Model, callback_hikari, init_hikari  # noqa: E402
from .yuyuko_llm import (  # noqa: E402
    COMMAND_BUILDERS,
    ERROR_EXCEPTION,
    ToolExecutor,
    ToolGuard,
    build_account_command,
    build_bind_command,
    build_literal_command,
    build_recent_command,
    build_roll_command,
    build_ship_command,
    build_ship_recent_command,
    build_ships_command,
    classify_error,
    user_error_text,
)

COMMAND = "wws"
# AstrBot 平台适配器 -> hikari 的 platform 取值
PLATFORM_MAP = {
    "aiocqhttp": "QQ",
    "qq_official": "QQ_OFFICIAL",
}

# 近期查询的天数上限（防止模型填 99999 把上游拖死）
MAX_RECENT_DAYS = 90


def tool_guard(func):
    """LLM 工具的最外层兜底：绝不把异常抛给框架。

    ▍为什么必须有这一层
    AstrBot 的 ``call_local_llm_tool`` 会把工具抛出的异常包成
    ``Exception(f"Tool execution error: {e}. Traceback: {trace_}")`` 直接回给模型
    （见 ``astrbot/core/astr_agent_tool_exec.py``）。那意味着模型会看到一大段
    英文堆栈，然后照着重述给用户 —— 用户只会更困惑。

    所以这里自己接住：按故障类型给出**行动指引**，让模型说人话。

    用 ``functools.wraps`` 保留原签名与 docstring：``@llm_tool`` 是靠 docstring
    生成参数 schema 的，签名丢了工具参数就会变空。
    """

    @functools.wraps(func)
    async def wrapper(self, event, *args, **kwargs):
        try:
            return await func(self, event, *args, **kwargs)
        except Exception as e:
            kind = classify_error(e)
            message = f"LLM 工具 {func.__name__} 执行异常（{kind}）: {e!r}"
            # 程序异常按 error 记（要排查），网络类按 warning 记（可重试）。
            # 注意 AstrBot 的 logger.log 要整数级别，别传 "error" 这样的字符串。
            if kind == ERROR_EXCEPTION:
                logger.error(message, exc_info=True)
            else:
                logger.warning(message)
            return user_error_text(kind)

    return wrapper


def _platform_of(event: AstrMessageEvent) -> str | None:
    """当前消息平台对应的 hikari platform 值；不支持时返回 None。"""
    return PLATFORM_MAP.get(event.get_platform_name())


def _identity(server: str | None, nickname: str | None) -> tuple[str | None, str | None]:
    """统一「查自己 / 查别人」的口径：只给一个也算给了，交给拼装层处理。"""
    server = (server or "").strip() or None
    nickname = (nickname or "").strip() or None
    return server, nickname


def _clamp_days(days: int | None) -> int | None:
    """把天数夹到合理区间；非正数当作没给。"""
    try:
        value = int(days) if days is not None else 0
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return min(value, MAX_RECENT_DAYS)


class WowsYuyuko(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.runtime = CoreRuntime(config)
        self.selection = SelectionManager()
        self.sender = OutputSender()
        self.guard = ToolGuard(config)
        self.tools = ToolExecutor(
            runtime=self.runtime,
            sender=self.sender,
            guard=self.guard,
            platform_for=_platform_of,
            config=config,
        )

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
            self.guard.reset()
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
        platform = _platform_of(event)
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

        async with self.runtime.render_lock:
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

    # =======================================================================
    # LLM 工具（function-calling）
    #
    # ▍为什么每个方法都要写一遍参数
    # @llm_tool 用 docstring 生成 JSON Schema（astrbot/core/star/register/star_handler.py
    # 的 register_llm_tool 解析 `Args:` 段），schema 就是模型看到的全部信息，
    # 所以参数必须显式声明 —— 帮不了它做动态生成。
    #
    # ▍返回值约定
    # 返回 str 会进入下一轮 LLM 上下文（模型据此作答/点评）；
    # 返回 None 则不进上下文。这里都返回文本，让模型能自然点评。
    # 图片由 ToolExecutor 直接发给用户，与 /wws 指令的排版完全一致。
    #
    # ▍开关
    # 每个工具在 ToolGuard 里都有独立开关，执行时统一判断；关闭时返回一句说明而不是
    # 静默失败，免得模型以为自己参数填错了。参数结构：server（服务器）、
    # nickname（游戏昵称），两者都留空 = 查当前用户自己（用 TA 绑定的账号）。
    # =======================================================================

    @llm_tool(name="wws_account")
    @tool_guard
    async def wws_account(
        self,
        event: AstrMessageEvent,
        server: str = None,
        nickname: str = None,
    ) -> str:
        """查询战舰世界玩家的战绩总表（总体水表）：总场次、胜率、场均伤害、PR 等。

        当用户问「我的战绩怎么样」「查一下 XX 的水表」「我玩得如何」时调用。
        返回的数据包含各战斗模式（随机 / 单野 / 自行车 / 三轮车 / 排位）的统计，
        以及逐舰种、逐等级的场次分布。拿到后请用自然语言点评玩家的水平和风格。

        Args:
            server(string): 服务器名（亚服/国服/欧服/美服/俄服）。不填则查当前用户自己。
            nickname(string): 游戏昵称。不填则查当前用户自己。
        """
        server, nickname = _identity(server, nickname)
        return await self.tools.execute(
            event, build_account_command(server, nickname), "account", "wws_account"
        )

    @llm_tool(name="wws_ship")
    @tool_guard
    async def wws_ship(
        self,
        event: AstrMessageEvent,
        ship: str,
        server: str = None,
        nickname: str = None,
    ) -> str:
        """查询某一条指定战舰的战绩（该玩家在这条船上的表现）。

        当用户问「我的俾斯麦打得怎么样」「XX 这条船战绩如何」时调用。
        注意：如果用户只是想看最近玩过哪些船，应该改用 wws_ship_recent。

        Args:
            ship(string): 战舰名称，必须提供（中英文均可，如 俾斯麦 / Bismarck）。
            server(string): 服务器名（亚服/国服/欧服/美服/俄服）。不填则查当前用户自己。
            nickname(string): 游戏昵称。不填则查当前用户自己。
        """
        server, nickname = _identity(server, nickname)
        return await self.tools.execute(
            event, build_ship_command(ship, server, nickname), "ship", "wws_ship"
        )

    @llm_tool(name="wws_recent")
    @tool_guard
    async def wws_recent(
        self,
        event: AstrMessageEvent,
        days: int = None,
        battle_mode: str = None,
        server: str = None,
        nickname: str = None,
    ) -> str:
        """查询玩家最近的战绩汇总（默认最近 7 天，也可按天数指定范围）。

        这是最常用的近期查询：返回该时间范围内各战斗模式的统计与逐船明细。
        当用户问「我最近打得怎么样」「今天战绩如何」「这周水表」时调用。

        Args:
            days(number): 统计最近多少天，默认 7，最大 90。
            battle_mode(string): 只关注某个模式时填写，可选：随机（不限人数的全部随机战）、单野（1 人玩）、自行车（2 人组队）、三轮车（3 人组队）、排位。不填则返回全部模式，建议不填，由你根据数据自行判断该点评哪个。
            server(string): 服务器名（亚服/国服/欧服/美服/俄服）。不填则查当前用户自己。
            nickname(string): 游戏昵称。不填则查当前用户自己。
        """
        server, nickname = _identity(server, nickname)
        return await self.tools.execute(
            event,
            build_recent_command(_clamp_days(days), server, nickname),
            "recent",
            "wws_recent",
            battle_mode=battle_mode,
        )

    @llm_tool(name="wws_recent_random")
    @tool_guard
    async def wws_recent_random(
        self,
        event: AstrMessageEvent,
        days: int = None,
        server: str = None,
        nickname: str = None,
    ) -> str:
        """查询玩家最近的随机战战绩（仅统计随机战 PVP，不含排位）。

        当用户明确说「最近随机战」「随机打得怎么样」时调用。
        如果用户只是泛泛地说「最近战绩」，应该优先用 wws_recent。

        Args:
            days(number): 统计最近多少天，默认 7，最大 90。
            server(string): 服务器名（亚服/国服/欧服/美服/俄服）。不填则查当前用户自己。
            nickname(string): 游戏昵称。不填则查当前用户自己。
        """
        server, nickname = _identity(server, nickname)
        command = build_recent_command(
            _clamp_days(days), server, nickname, mode=COMMAND_BUILDERS["recent_random"]
        )
        return await self.tools.execute(event, command, "recent", "wws_recent_random")

    @llm_tool(name="wws_recent_rank")
    @tool_guard
    async def wws_recent_rank(
        self,
        event: AstrMessageEvent,
        days: int = None,
        server: str = None,
        nickname: str = None,
    ) -> str:
        """查询玩家最近的排位战战绩。

        当用户说「最近排位」「排位打得怎么样」时调用。

        Args:
            days(number): 统计最近多少天，默认 7，最大 90。
            server(string): 服务器名（亚服/国服/欧服/美服/俄服）。不填则查当前用户自己。
            nickname(string): 游戏昵称。不填则查当前用户自己。
        """
        server, nickname = _identity(server, nickname)
        command = build_recent_command(
            _clamp_days(days), server, nickname, mode=COMMAND_BUILDERS["recent_rank"]
        )
        return await self.tools.execute(event, command, "recent", "wws_recent_rank")

    @llm_tool(name="wws_ship_recent")
    @tool_guard
    async def wws_ship_recent(
        self,
        event: AstrMessageEvent,
        ship: str,
        days: int = None,
        server: str = None,
        nickname: str = None,
    ) -> str:
        """查询玩家在某条指定战舰上的最近战绩（单船 + 指定时间段）。

        当用户问「我最近开俾斯麦打得怎么样」「XX 这条船这周表现」时调用。
        必须给出船名；如果用户没提船名，应该改用 wws_recent。

        Args:
            ship(string): 战舰名称，必须提供（如 俾斯麦 / Bismarck）。
            days(number): 统计最近多少天，默认 7，最大 90。
            server(string): 服务器名（亚服/国服/欧服/美服/俄服）。不填则查当前用户自己。
            nickname(string): 游戏昵称。不填则查当前用户自己。
        """
        server, nickname = _identity(server, nickname)
        command = build_ship_recent_command(ship, _clamp_days(days), server, nickname)
        return await self.tools.execute(event, command, "recent", "wws_ship_recent")

    @llm_tool(name="wws_recent_battles")
    @tool_guard
    async def wws_recent_battles(
        self,
        event: AstrMessageEvent,
        server: str = None,
        nickname: str = None,
    ) -> str:
        """查询玩家逐场的近期战斗明细（每一场的时间、结果、伤害等），而不是汇总统计。

        当用户问「我最近这场打得怎么样」「刚才那把如何」「逐场看看」时调用。
        如果用户只是想看总体近况，用 wws_recent 更合适（信息更聚合）。

        Args:
            server(string): 服务器名（亚服/国服/欧服/美服/俄服）。不填则查当前用户自己。
            nickname(string): 游戏昵称。不填则查当前用户自己。
        """
        server, nickname = _identity(server, nickname)
        return await self.tools.execute(
            event, build_literal_command("recents", server, nickname), "recents",
            "wws_recent_battles",
        )

    @llm_tool(name="wws_ships")
    @tool_guard
    async def wws_ships(
        self,
        event: AstrMessageEvent,
        min_level: int = None,
        max_level: int = None,
        nation: str = None,
        ship_type: str = None,
        server: str = None,
        nickname: str = None,
    ) -> str:
        """按条件筛选玩家的战舰列表及各自的战绩，可筛等级区间、国家、舰种。

        当用户问「我 10 级战列舰都打得怎么样」「日系船的战绩」「8 到 10 级的巡洋舰」
        时调用。返回的列表可能较长，请自行归纳要点，不要把整张表念给用户。

        Args:
            min_level(number): 最低等级（1-11），不填为 5。
            max_level(number): 最高等级（1-11），不填为不限。
            nation(string): 国家/系别，如 日本 / 美国 / 苏联 / 德国 / 英国 / 法国 / 意大利 / 泛亚 / 欧洲 / 荷兰 / 西班牙 / 泛美。
            ship_type(string): 舰种，如 战列舰 / 巡洋舰 / 驱逐舰 / 航空母舰 / 潜艇。
            server(string): 服务器名（亚服/国服/欧服/美服/俄服）。不填则查当前用户自己。
            nickname(string): 游戏昵称。不填则查当前用户自己。
        """
        server, nickname = _identity(server, nickname)
        return await self.tools.execute(
            event,
            build_ships_command(
                min_level, max_level, nation, ship_type, server, nickname
            ),
            "ships",
            "wws_ships",
        )

    @llm_tool(name="wws_roll_ship")
    @tool_guard
    async def wws_roll_ship(
        self,
        event: AstrMessageEvent,
        nation: str = None,
        ship_type: str = None,
        level: int = None,
    ) -> str:
        """随机抽一条战舰（舰船抽奖 / 娱乐功能）。

        当用户说「随机来条船」「抽个船玩玩」「今天开什么船」时调用。
        可以按国家、舰种、等级缩小范围，都不填就是从全部战舰里随机。

        Args:
            nation(string): 限定国家/系别，如 日本 / 美国 / 苏联 / 德国 / 英国。
            ship_type(string): 限定舰种，如 战列舰 / 巡洋舰 / 驱逐舰 / 航空母舰 / 潜艇。
            level(number): 限定等级（1-11）。
        """
        return await self.tools.execute(
            event, build_roll_command(nation, ship_type, level), "roll", "wws_roll_ship"
        )

    @llm_tool(name="wws_bind")
    @tool_guard
    async def wws_bind(
        self,
        event: AstrMessageEvent,
        nickname: str,
        server: str,
    ) -> str:
        """把游戏账号绑定到当前用户；绑定后该用户说「查我的战绩」时无需再提供昵称。

        以下情况应主动引导用户完成绑定：
        1) 用户要查自己的战绩但尚未绑定（查询返回未绑定提示时）；
        2) 用户直接说出了自己的游戏昵称，希望以后都能查；
        3) 用户问「怎么绑定」「为什么查不了我的」。
        引导时先问清楚昵称和服务器，别让用户自己猜参数格式。

        Args:
            nickname(string): 用户的游戏昵称，必须提供。
            server(string): 账号所在服务器，必须提供：亚服 / 国服 / 欧服 / 美服 / 俄服。
        """
        return await self.tools.execute(
            event, build_bind_command(server, nickname), "bind", "wws_bind"
        )

    @llm_tool(name="wws_bind_list")
    @tool_guard
    async def wws_bind_list(self, event: AstrMessageEvent) -> str:
        """查询当前用户已经绑定了哪些游戏账号。

        当用户问「我绑定了什么」、绑定失败怀疑绑错、或查询结果与预期不符时调用。
        """
        return await self.tools.execute(
            event, COMMAND_BUILDERS["bind_list"], "bind", "wws_bind_list"
        )

    @llm_tool(name="wws_bind_change")
    @tool_guard
    async def wws_bind_change(
        self,
        event: AstrMessageEvent,
        select_index: int = None,
    ) -> str:
        """在用户已绑定的多个游戏账号之间切换当前使用的账号。

        需要用户已绑定两个以上账号。用法分两步：
        先不带 select_index 调用本工具，会返回绑定列表；
        你把列表转述给用户，用户选定后再带 select_index 调用一次完成切换。

        Args:
            select_index(number): 要切换到的序号（从 1 开始）。不填则只返回绑定列表供用户选择。
        """
        if select_index is None:
            # 第一步：先给出绑定列表（走 bind_list 而不是 change_bind，
            # 后者不带序号时会返回「需要选择」的候选项列表，不够直观）
            return await self.tools.execute(
                event, COMMAND_BUILDERS["bind_list"], "bind", "wws_bind_list"
            )
        return await self.tools.execute(
            event,
            COMMAND_BUILDERS["bind_change"],
            "bind",
            "wws_bind_change",
            select_index=select_index,
        )

