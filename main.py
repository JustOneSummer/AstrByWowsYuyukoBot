import shutil
from collections import defaultdict, namedtuple
from hashlib import md5

import asyncio
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star import StarTools
from hikari_core import Hikari_Model, init_hikari, callback_hikari, hikari_config
from hikari_core.cache_utils import get_cache_file
from hikari_core.config import set_hikari_config

SelectState = namedtuple('SelectState', ['state', 'index', 'list'])
SelectProcess = defaultdict[str, SelectState](lambda: SelectState(False, None, None))

class WowsYuyuko(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config

    async def initialize(self):
        """初始化"""
        try:
            yuyuko_type = 'AstrBot'
            use_broswer = self.config.get("use_broswer")
            http_status = self.config.get("http2_status")
            local_test = self.config.get("local_test")
            wows_token = self.config.get("wows_token")
            wows_proxy_status = self.config.get("wows_proxy_status")
            wows_proxy = self.config.get("wows_proxy")
            if wows_proxy_status:
                set_hikari_config(use_broswer=use_broswer, http2=http_status, proxy=wows_proxy,
                                  local_test=local_test,
                                  token=wows_token, yuyuko_type=yuyuko_type,
                                  game_path=str(StarTools.get_data_dir("wows-yuyuko")))
            else:
                set_hikari_config(use_broswer=use_broswer, http2=http_status, proxy=None,
                                  local_test=local_test,
                                  token=wows_token, yuyuko_type=yuyuko_type,
                                  game_path=str(StarTools.get_data_dir("wows-yuyuko")))
            temp_dir = get_cache_file() / "file_img_temp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            logger.info("wows-yuyuko插件初始化成功")
        except Exception as e:
            logger.error(f"wows-yuyuko插件初始化失败: {e}")

    # 注册指令的装饰器。
    @filter.command("wws")
    async def wws(self, event: AstrMessageEvent):
        try:
            "识别平台"
            if event.get_platform_name() == "aiocqhttp":
                platform = "QQ"
            else:
                await event.send(MessageChain().message(f"不支持的平台消息 name={event.get_platform_name()}"))
                return None
            "开始处理用户发送的指令"
            message_str = remove_command_prefix(event.message_str)  # 用户发的纯文本消息字符串
            hikari = await init_hikari(command_text=message_str, platform=platform, PlatformId=event.get_sender_id(), GroupId=event.get_group_id())
            if hikari.Status == 'success':
                await event.send(output_send_img(event, hikari))
                return None
            if hikari.Status == 'wait':
                await event.send(output_send_img(event, hikari))
                await wait_to_select(hikari)
                if hikari.Status == 'error':
                    await event.send(output_send_img(event, hikari))
                    return None
                hikari = await callback_hikari(hikari)
                await event.send(output_send_img(event, hikari))
            else:
                await event.send(output_send_img(event, hikari))
        except Exception as e:
            logger.exception(f"指令处理异常 {e}")
            await event.send(MessageChain().message("呜呜呜发生了错误，可能是网络问题，如果过段时间不能恢复请联系麻麻哦~"))
        return None

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def change_select_state(self, event: AstrMessageEvent):
        try:
            msg = str(event.message_str)
            qqid = str(event.get_sender_id())
            if SelectProcess[qqid].state and str(msg).isdigit():
                if int(msg) <= len(SelectProcess[qqid].list):
                    SelectProcess[qqid] = SelectProcess[qqid]._replace(state=False)
                    SelectProcess[qqid] = SelectProcess[qqid]._replace(index=int(msg))
                else:
                    await event.send(MessageChain().at(event.get_sender_name(), event.get_sender_id()).message("请选择列表中的序号哦~"))
            return None
        except Exception as e:
            logger.exception(f"指令选择器处理异常 {e}")
            return None

    async def terminate(self):
        try:
            """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
            logger.info("开始执行wows-yuyuko插件临时资源销毁")
            temp_dir = get_cache_file() / "file_img_temp"
            if temp_dir.exists() and temp_dir.is_dir():
                # 删除整个目录及其所有内容
                shutil.rmtree(temp_dir)
                print(f"已删除file_img_temp目录: {temp_dir}")
            logger.info("wows-yuyuko插件临时资源销毁完成")
        except Exception as e:
            logger.error(f"删除file_img_temp目录失败: {e}")


def wows_img_file_temp(hikari_data: Hikari_Model):
    obj = md5()
    obj.update(hikari_data.Input.Command_Text.encode("utf-8"))
    hash_md5 = obj.hexdigest()
    return get_cache_file() / "file_img_temp" / f"{hikari_data.UserInfo.Platform}_{hikari_data.UserInfo.PlatformId}_{hikari_data.UserInfo.GroupId}-hash_{hash_md5}.jpg"


def output_send_img(event: AstrMessageEvent, hikari_data: Hikari_Model) -> MessageChain:
    if isinstance(hikari_data.Output.Data, bytes):
        img = str(wows_img_file_temp(hikari_data))
        if hikari_config.local_test:
            with open(img + '.html', 'w', encoding='utf-8') as f:
                f.write(hikari_data.template_content)
        with open(img, 'wb') as f:
            f.write(hikari_data.Output.Data)
        return MessageChain().at(name=event.get_sender_name(), qq=event.get_sender_id()).file_image(img)
    elif isinstance(hikari_data.Output.Data, str):
        return MessageChain().at(name=event.get_sender_name(), qq=event.get_sender_id()).message(hikari_data.Output.Data)
    else:
        return MessageChain().at(name=event.get_sender_name(), qq=event.get_sender_id()).message(f"未知数据类型标记 {hikari_data.Output.Data_Type}")


async def wait_to_select(hikari):
    SelectProcess[hikari.UserInfo.PlatformId] = SelectState(True, None, hikari.Input.Select_Data)
    a = 0
    while a < 40 and not SelectProcess[hikari.UserInfo.PlatformId].index:
        a += 1
        await asyncio.sleep(0.5)
    if SelectProcess[hikari.UserInfo.PlatformId].index:
        hikari.Input.Select_Index = SelectProcess[hikari.UserInfo.PlatformId].index
        SelectProcess[hikari.UserInfo.PlatformId] = SelectState(False, None, None)
        return hikari
    else:
        SelectProcess[hikari.UserInfo.PlatformId] = SelectState(False, None, None)
        return hikari.error('已超时退出')


def remove_command_prefix(message_str: str, commands: list = None) -> str:
    """
    移除消息中的命令前缀

    Args:
        message_str: 原始消息字符串
        commands: 要移除的命令列表

    Returns:
        清理后的消息内容
    """
    if commands is None:
        commands = ['/wws', 'wws']
    message_str = message_str.strip()
    # 按优先级检查并移除命令前缀
    for cmd in commands:
        # 检查是否以命令开头（带空格或不带空格）
        if message_str.startswith(cmd):
            # 移除命令
            remaining = message_str[len(cmd):].strip()
            # 如果命令后有空格，确保移除多余空格
            if remaining.startswith(' '):
                remaining = remaining[1:]
            return remaining

        # 处理命令后跟其他符号的情况，如 /wws@bot
        if message_str.startswith(f"{cmd}@"):
            # 找到命令后的第一个空格或结尾
            idx = len(cmd)
            # 跳过@和机器人名
            while idx < len(message_str) and message_str[idx] != ' ':
                idx += 1
            remaining = message_str[idx:].strip()
            return remaining

    return message_str
