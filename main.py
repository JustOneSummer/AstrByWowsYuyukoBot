from collections import defaultdict
from hashlib import md5
from typing import NamedTuple

import aiofiles
import asyncio
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star import StarTools
from hikari_core import Hikari_Model, init_hikari, callback_hikari, hikari_config
from hikari_core.cache_utils import get_cache_file
from hikari_core.config import set_hikari_config


class SelectState(NamedTuple):
    state: bool
    index: int | None
    list: list | None
    event: asyncio.Event | None = None


class WowsYuyuko(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.select_process = defaultdict[str, SelectState](
            lambda: SelectState(False, None, None, None)
        )

    async def initialize(self):
        """初始化"""
        try:
            logger.info("开始初始化wows-yuyuko插件")
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
            message_str = self._remove_command_prefix(event.message_str)  # 用户发的纯文本消息字符串
            hikari = await init_hikari(command_text=message_str, platform=platform, PlatformId=event.get_sender_id(), GroupId=event.get_group_id())
            if hikari.Status == 'success':
                await self.output_send_img(event=event, hikari_data=hikari)
                return None
            if hikari.Status == 'wait':
                await self.output_send_img(event=event, hikari_data=hikari)
                await self._wait_to_select(hikari)
                if hikari.Status == 'error':
                    await self.output_send_img(event=event, hikari_data=hikari)
                    return None
                hikari = await callback_hikari(hikari)
                await self.output_send_img(event=event, hikari_data=hikari)
            else:
                await self.output_send_img(event=event, hikari_data=hikari)
        except Exception as e:
            logger.exception(f"指令处理异常 {e}")
            await event.send(MessageChain().message("呜呜呜发生了错误，可能是网络问题，如果过段时间不能恢复请联系麻麻哦~"))
        return None

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def change_select_state(self, event: AstrMessageEvent):
        try:
            msg = str(event.message_str)
            user_id = str(event.get_sender_id())
            state = self.select_process[user_id]
            # 如果用户在选择状态中且输入是数字
            if state.state and msg.isdigit():
                choice = int(msg)

                # 检查选择是否有效
                if 1 <= choice <= len(state.list):
                    # 更新状态并触发事件
                    self.select_process[user_id] = state._replace(
                        state=False,
                        index=choice
                    )
                    # 触发事件
                    if state.event:
                        state.event.set()
                else:
                    # 无效选择，提示用户
                    await event.send(
                        MessageChain()
                        .at(event.get_sender_name(), event.get_sender_id())
                        .message(f"请选择 1-{len(state.list)} 之间的序号")
                    )
            return None
        except Exception as e:
            logger.exception(f"指令选择器处理异常 {e}")
            return None

    async def terminate(self):
        try:
            # 插件卸载时执行
            return None
        except Exception as e:
            logger.error(f"删除file_img_temp目录失败: {e}")

    async def output_send_img(self, event: AstrMessageEvent, hikari_data: Hikari_Model):
        if isinstance(hikari_data.Output.Data, bytes):
            asyncio.create_task(self._async_save_files(event, hikari_data))
        elif isinstance(hikari_data.Output.Data, str):
            await event.send(MessageChain().at(name=event.get_sender_name(), qq=event.get_sender_id()).message(hikari_data.Output.Data))
        else:
            await event.send(MessageChain().at(name=event.get_sender_name(), qq=event.get_sender_id()).message(f"未知数据类型标记 {hikari_data.Output.Data_Type}"))

    async def _async_save_files(self, event: AstrMessageEvent, hikari_data: Hikari_Model):
        """异步保存文件并发送"""
        try:
            # 生成文件名
            img_path = str(self._wows_img_file_temp(hikari_data))
            if hikari_config.local_test:
                # 异步写入HTML文件
                async with aiofiles.open(img_path + '.html', 'w', encoding='utf-8') as f:
                    await f.write(hikari_data.template_content)
            # 异步写入图片文件
            async with aiofiles.open(img_path, 'wb') as f:
                await f.write(hikari_data.Output.Data)
            # 发送图片消息
            message_chain = MessageChain().at(name=event.get_sender_name(),
                                              qq=event.get_sender_id()).file_image(img_path)
            await event.send(message_chain)
        except Exception as e:
            logger.exception(f"保存文件异常 {e}")

    async def _wait_to_select(self, hikari):
        user_id = hikari.UserInfo.PlatformId
        # 创建新的事件
        event = asyncio.Event()
        # 存储状态（包含事件）
        self.select_process[user_id] = SelectState(
            state=True,
            index=None,
            list=hikari.Input.Select_Data,
            event=event
        )
        try:
            # 等待事件
            try:
                await asyncio.wait_for(event.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                return hikari.error('已超时退出')

            # 获取当前状态
            state = self.select_process[user_id]
            if state.index is not None:
                hikari.Input.Select_Index = state.index
                return hikari

        finally:
            # 清理：设置状态为完成
            self.select_process[user_id] = SelectState(
                state=False,
                index=None,
                list=None,
                event=None
            )

        return hikari.error('选择无效')

    def _wows_img_file_temp(self, hikari_data: Hikari_Model):
        obj = md5()
        obj.update(hikari_data.Input.Command_Text.encode("utf-8"))
        hash_md5 = obj.hexdigest()
        return get_cache_file() / "file_img_temp" / f"{hikari_data.UserInfo.Platform}_{hikari_data.UserInfo.PlatformId}_{hikari_data.UserInfo.GroupId}-hash_{hash_md5}.jpg"

    def _remove_command_prefix(self, message_str: str, commands: list = None) -> str:
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
