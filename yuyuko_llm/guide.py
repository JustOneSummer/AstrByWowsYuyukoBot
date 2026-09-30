"""LLM 工具的开关、限流与话术处理。

三件事：

1. **开关**：总开关 + 每个工具的独立开关（配置见 ``_conf_schema.json``）。
2. **限流**：LLM 可能被引导反复调用工具（刷屏 / 打爆上游接口），按会话限制频率。
3. **话术**：把 hikari 的机读错误（如「请先绑定」）换成给模型的**行动指引**
   （比如引导用户去绑定），而不是让模型把原始报错念给用户。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from astrbot.api import logger

from .tool_spec import BIND_GUIDE, UNBOUND_MARKERS

__all__ = ["ToolGuard", "humanize_error"]


# ===========================================================================
# 配置读取
# ===========================================================================

# 工具名 -> 配置里的开关字段。加工具时在这里登记，默认值统一为 True。
TOOL_SWITCH_FIELDS: dict[str, str] = {
    "wws_account": "llm_tool_enable_account",
    "wws_ship": "llm_tool_enable_ship",
    "wws_recent": "llm_tool_enable_recent",
    "wws_recent_random": "llm_tool_enable_recent_random",
    "wws_recent_rank": "llm_tool_enable_recent_rank",
    "wws_ship_recent": "llm_tool_enable_ship_recent",
    "wws_recent_battles": "llm_tool_enable_recent_battles",
    "wws_ships": "llm_tool_enable_ships",
    "wws_roll_ship": "llm_tool_enable_roll_ship",
    "wws_bind": "llm_tool_enable_bind",
    "wws_bind_list": "llm_tool_enable_bind_list",
    "wws_bind_change": "llm_tool_enable_bind_change",
}


def config_get(config, key: str, default=None):
    """从 AstrBot 配置里取值，兼容对象与普通映射两种形态。"""
    if config is None:
        return default
    try:
        getter = getattr(config, "get", None)
        if callable(getter):
            value = getter(key, default)
            return default if value is None else value
        return getattr(config, key, default)
    except Exception:
        return default


def config_get_bool(config, key: str, default: bool = True) -> bool:
    """取布尔配置，容忍 "false" / "0" 这类字符串写法。"""
    value = config_get(config, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    if value is None:
        return default
    return bool(value)


def config_get_int(config, key: str, default: int) -> int:
    """取整数配置，非法值回退默认。"""
    value = config_get(config, key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ===========================================================================
# 话术
# ===========================================================================

# hikari 的机读错误 -> 给模型的行动指引。
# 命中后模型应该照指引去做（比如引导用户绑定），而不是把原文念给用户。
_ERROR_GUIDES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        UNBOUND_MARKERS,
        BIND_GUIDE,
    ),
    (
        ("服务器名", "服务器输入错误"),
        "服务器名无法识别。请告诉用户可用的服务器名（亚服 / 国服 / 欧服 / 美服 / 俄服），"
        "确认后重新调用工具；如果用户其实是要查自己，改为不传 server 和 nickname。",
    ),
    (
        ("未识别", "请发送wws help", "无法识别参数"),
        "指令参数无法识别。请检查你拼的参数是否符合工具说明，"
        "或者询问用户更明确的信息后重试。",
    ),
    (
        ("没有随机战数据记录", "没有排位战数据记录", "数据记录不存在"),
        "该时间范围内没有对应模式的战斗记录。请如实告诉用户这段时间没有打，"
        "可以建议换一个天数范围（例如从 7 天改成 30 天）再查。",
    ),
    (
        ("仅管理员", "该指令仅管理员可用"),
        "这个功能仅机器人管理员可用，请礼貌地告诉用户无法执行。",
    ),
)


def humanize_error(message: str) -> str:
    """把机读错误换成给模型的行动指引；识别不了就原样返回。

    刻意保留原文：模型拿到模糊错误时仍能如实转述，不会因为这里没登记就丢信息。
    """
    if not message:
        return "查询没有返回内容，请稍后重试。"
    text = str(message)
    for markers, guide in _ERROR_GUIDES:
        if any(marker in text for marker in markers):
            return f"{guide}\n（原始提示：{text}）"
    return text


# ===========================================================================
# 限流
# ===========================================================================


class ToolGuard:
    """按会话限制工具调用频率。"""

    def __init__(self, config, window_seconds: float = 60.0):
        self._config = config
        self._window = window_seconds
        # 会话 -> 最近的调用时间戳（只留窗口内的，天然不增长）
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    @property
    def limit(self) -> int:
        """每个窗口内允许的调用次数；<=0 表示不限流。"""
        return config_get_int(self._config, "llm_tool_rate_limit", 10)

    def enabled(self) -> bool:
        """LLM 工具总开关。"""
        return config_get_bool(self._config, "llm_tool_enable", True)

    def tool_enabled(self, tool_name: str) -> bool:
        """单个工具的开关；未登记的工具体默认放行。"""
        field = TOOL_SWITCH_FIELDS.get(tool_name)
        if not field:
            return True
        return config_get_bool(self._config, field, True)

    def check(self, session: str) -> str | None:
        """记录一次调用；超限时返回给用户的提示，未超限返回 None。"""
        limit = self.limit
        if limit <= 0:
            return None
        now = time.monotonic()
        bucket = self._calls[session]
        while bucket and now - bucket[0] > self._window:
            bucket.popleft()
        if len(bucket) >= limit:
            wait = max(1, int(self._window - (now - bucket[0])))
            logger.info(f"LLM 工具调用超限 session={session} limit={limit}/{self._window:.0f}s")
            return f"查询太频繁啦，请等 {wait} 秒后再试~"
        bucket.append(now)
        return None

    def reset(self) -> None:
        """清空限流记录（插件卸载时用）。"""
        self._calls.clear()
