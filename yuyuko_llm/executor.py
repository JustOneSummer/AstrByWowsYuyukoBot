"""LLM 工具的共享执行流程。

每个工具在 ``main.py`` 里只是一个薄壳（因为 ``@llm_tool`` 必须定义在那里），
真正的流程在这里，避免 11 份重复代码：

    限流检查 -> 查询（不渲染） -> 渲染发图给用户 -> 返回精简数据给 LLM

▍关于「发图给用户 + 给 LLM 文字」
图片对模型没有价值，但用户喜欢现有的排版。所以两边各拿各的：
用户收到和 ``wws`` 指令一模一样的渲染图，模型收到提取后的字段用来点评。
"""

from __future__ import annotations

import json
from typing import Any

from astrbot.api import logger

from .guide import ToolGuard, config_get_bool, humanize_error
from .query import QueryResult, QueryRunner

__all__ = ["ToolExecutor"]


# 模型侧文本的上限：数据本身不大，但异常情况下兜一下，避免把 prompt 撑爆
MAX_PAYLOAD_CHARS = 6000


class ToolExecutor:
    """把「一次工具调用」跑完：查数据、发图、组织给模型的返回值。"""

    def __init__(self, runtime, sender, guard: ToolGuard, platform_for, config=None):
        """
        Args:
            runtime: ``yuyuko_bot.runtime.CoreRuntime``，用来判断就绪与串行化渲染
            sender: ``yuyuko_bot.sender.OutputSender``
            guard: 限流与开关
            platform_for: 可调用对象，入参事件，返回 hikari 的 platform 值或 None
            config: AstrBot 配置（评价开关等）
        """
        self._runtime = runtime
        self._sender = sender
        self._guard = guard
        self._platform_for = platform_for
        self._config = config

    async def execute(
        self,
        event,
        command_text: str,
        extractor: str,
        tool_name: str = "",
        *,
        send_image: bool = True,
        select_index: int | None = None,
        battle_mode: str | None = None,
        **extract_kwargs: Any,
    ) -> str:
        """执行一次工具调用，返回给模型的文本。

        Args:
            tool_name: 工具名，用于查它自己的开关（见 ``guide.TOOL_SWITCH_FIELDS``）。
        """
        # 1) 总开关：关掉时给出明确说明，免得模型以为自己用错了参数
        if not self._guard.enabled():
            return "战绩查询工具当前已被机器人管理员关闭，请告知用户暂时无法查询。"

        # 2) 单个工具的开关
        if tool_name and not self._guard.tool_enabled(tool_name):
            return f"工具 {tool_name} 已被机器人管理员单独关闭，请告知用户该功能暂不可用。"

        # 3) 就绪门控：首次启动要下模板与战舰资源
        if not self._runtime.is_ready():
            return "战绩查询功能正在初始化（首次使用需下载资源），请让用户稍等一会儿再试。"

        # 4) 平台校验
        platform = self._platform_for(event)
        if platform is None:
            return "当前消息平台不支持战绩查询。"

        # 5) 限流
        session = event.unified_msg_origin
        limited = self._guard.check(session)
        if limited:
            return limited

        runner = QueryRunner(
            platform=platform,
            platform_id=event.get_sender_id(),
            bot_id=event.get_self_id(),
            group_id=event.get_group_id(),
        )

        result = await runner.run(
            command_text,
            extractor,
            select_index=select_index,
            battle_mode=battle_mode,
            **extract_kwargs,
        )

        # 6) 需要用户二次选择：把候选列表交给模型转述，不发图
        if result.need_select:
            return result.message

        if not result.ok:
            return self._format_error(result)

        # 7) 渲染发图：和 wws 指令走同一套渲染与发送逻辑
        if send_image:
            await self._send_rendered(runner, result, event)

        return self._format_payload(extractor, result)

    async def _send_rendered(self, runner: QueryRunner, result: QueryResult, event) -> None:
        """渲染并发送图片；失败只记日志，不影响给模型的文本。"""
        try:
            async with self._runtime.render_lock:
                data = await runner.render(result)
            if not data:
                return
            await self._sender.send_bytes(event, result.hikari, data)
        except Exception as e:
            logger.warning(f"LLM 工具发送图片失败: {e}")

    @staticmethod
    def _format_error(result: QueryResult) -> str:
        """失败信息交给话术层换成行动指引。"""
        return humanize_error(result.message)

    def _format_payload(self, extractor: str, result: QueryResult) -> str:
        """把提取结果压成给模型的文本。"""
        payload = result.data or {}
        if not payload:
            return "查询成功，但没有提取到可读数据（可能是该账号在该条件下没有记录），请如实告知用户。"

        try:
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(payload)
        if len(text) > MAX_PAYLOAD_CHARS:
            text = text[:MAX_PAYLOAD_CHARS] + "…（数据过长已截断）"

        head = "以下是用户要查询的战绩数据（JSON），请据此作答"
        if self._evaluate_enabled():
            head += "，并用自然的语气点评一下玩家的表现（水平、风格、亮点或不足）"
        head += "。图片已经发给用户了，不需要再把数据罗列一遍。\n\n"
        return head + text

    def _evaluate_enabled(self) -> bool:
        """评价开关；关掉时只让模型转述数据，不引导点评。"""
        return config_get_bool(self._config, "llm_tool_evaluate", True)
