"""把 hikari-core 的渲染结果发给用户。

v2 的 `Output.Data` 有三种形态：`bytes`（截图）、`str`（文本错误 / 提示）、
其它（未渲染的数据结构，兜底提示）。图片走「先落盘再按文件发送」。

**为什么要落盘**：`MessageChain.file_image(path)` 只是把路径交给平台适配器，
真正读盘发生在 `event.send()` 之后、由协议端完成，所以文件在发送后也不能删。
文件名带内容哈希，同一条指令重复查询天然复用同一份；退出插件时整体清理。
"""

import asyncio
from hashlib import md5
from pathlib import Path

import aiofiles

from astrbot.api import logger
from astrbot.core.message.message_event_result import MessageChain

from hikari_core import Hikari_Model, hikari_config

from .runtime import get_temp_dir

# v2 可能输出的图片格式（对应 hikari_config.image_type）
IMAGE_SUFFIXES = {"jpeg", "jpg", "png", "webp"}


class OutputSender:
    """负责渲染结果的落盘与发送。"""

    def __init__(self):
        # 发后不管的图片发送任务，持引用避免被 GC 提前回收
        self._bg_tasks: set[asyncio.Task] = set()

    async def send(self, event, hikari_data: Hikari_Model) -> None:
        """按 `Output.Data` 的实际类型选择发送方式。"""
        data = hikari_data.Output.Data
        if isinstance(data, bytes):
            task = asyncio.create_task(
                self._save_and_send_image(event, hikari_data, data)
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        elif isinstance(data, str):
            await reply_text(event, data)
        else:
            await reply_text(event, f"未知数据类型标记 {hikari_data.Output.Data_Type}")

    async def send_bytes(self, event, hikari_data: Hikari_Model, data: bytes) -> None:
        """直接把一段图片字节落盘并发给用户。

        给 LLM 工具用：那边是自己调 ``output_hikari`` 拿到 bytes，
        没有走 ``send()`` 那条「从 Output.Data 取数据」的路径。
        落盘与命名复用同一套逻辑（内容哈希，重复查询自动复用同一份文件）。
        """
        await self._save_and_send_image(event, hikari_data, data)

    async def _save_and_send_image(self, event, hikari_data: Hikari_Model, data: bytes):
        """图片先落盘再按文件发送（调试模式下顺带留一份 HTML 供排查）。"""
        try:
            img_path = render_output_path(hikari_data, data)
            # 目录正常情况下由 CoreRuntime.start() 建好；这里再兜一次底，
            # 万一被清理掉也不会因为一个目录丢失就发不出图
            await asyncio.to_thread(img_path.parent.mkdir, parents=True, exist_ok=True)
            if hikari_config.local_test:
                async with aiofiles.open(f"{img_path}.html", 'w', encoding='utf-8') as f:
                    await f.write(hikari_data.template_content)
            async with aiofiles.open(img_path, 'wb') as f:
                await f.write(data)
            await event.send(
                MessageChain()
                .at(name=event.get_sender_name(), qq=event.get_sender_id())
                .file_image(str(img_path))
            )
        except asyncio.CancelledError:
            # 插件卸载时主动取消，属于预期路径
            raise
        except Exception as e:
            logger.exception(f"保存/发送图片异常 {e}")

    async def cancel_pending(self) -> None:
        """取消并等待所有在途的图片发送任务。"""
        tasks = list(self._bg_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            # 取消后 done_callback 不一定会跑（任务可能根本没轮到执行），显式清空，
            # 免得已死的 Task 对象一直挂在集合里
            self._bg_tasks.difference_update(tasks)


async def reply_text(event, text: str) -> None:
    """给触发指令的用户回一条纯文本（带 @）。"""
    await event.send(
        MessageChain()
        .at(name=event.get_sender_name(), qq=event.get_sender_id())
        .message(text)
    )


def render_output_path(hikari_data: Hikari_Model, data: bytes) -> Path:
    """渲染产物的落盘路径。

    文件名取「会话标识 + 图片内容哈希」：同一条指令重复渲染会落到同一路径，
    并发写也不会互相踩（内容一致，覆盖无害）；后缀跟随 v2 实际输出格式
    （jpeg/png/webp），别让 webp 顶着 .jpg 的壳。
    """
    user = hikari_data.UserInfo
    suffix = str(hikari_data.Output.Data_Type).strip().lower()
    if suffix not in IMAGE_SUFFIXES:
        suffix = 'jpg'
    return (
        get_temp_dir()
        / f"{user.Platform}_{user.PlatformId}_{user.GroupId or 'private'}-{md5(data).hexdigest()}.{suffix}"
    )
