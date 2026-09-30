"""把内置 hikari-core 的 loguru 日志接进 AstrBot。

hikari-core 裸用 loguru，而 AstrBot 的 sink 格式硬编码了 {extra[plugin_tag]}
等字段，缺了会抛 KeyError 并丢掉整条记录，所以 AstrBot 里看不到 hikari 的日志。
这里用 loguru core 级 patcher 补齐字段，并转发到 LogBroker（WebUI 面板数据源）。
"""

from __future__ import annotations

import os
import time
import traceback
from typing import Any

from loguru import logger as _loguru

PLUGIN_TAG = "[astrbot_plugin_wows_yuyuko]"
_HIKARI_PREFIX = "hikari_core"

_SHORT_LEVEL = {
    "TRACE": "TRCE",
    "DEBUG": "DBUG",
    "INFO": "INFO",
    "SUCCESS": "SUCC",
    "WARNING": "WARN",
    "ERROR": "ERRO",
    "CRITICAL": "CRIT",
}

_LEVEL_COLOR = {
    "DEBUG": "\u001b[1;34m",
    "INFO": "\u001b[1;36m",
    "WARNING": "\u001b[1;33m",
    "ERROR": "\u001b[31m",
    "CRITICAL": "\u001b[1;31m",
}

try:  # 取不到就只是少个 WARNING 以上的 [vX.Y.Z] 标记
    from astrbot.core.config.default import VERSION as _VERSION
except Exception:
    _VERSION = ""

_installed = False
_previous_patcher: Any = None
_log_broker: Any = None


def _short_level_name(level_name: str) -> str:
    return _SHORT_LEVEL.get(level_name, level_name[:4].upper())


def _source_file(path: str | None) -> str:
    """压成 AstrBot 的 "目录.文件名" 风格。"""
    if not path:
        return "unknown"
    directory, _, filename = os.path.normpath(path).replace("\\", "/").rpartition("/")
    parent = directory.rpartition("/")[2]
    stem = filename[:-3] if filename.endswith(".py") else filename
    return f"{parent}.{stem}" if parent else stem


def _is_hikari_record(record: dict) -> bool:
    name = record["name"] or ""
    if name == _HIKARI_PREFIX or name.startswith(_HIKARI_PREFIX + "."):
        return True
    return f"/{_HIKARI_PREFIX}/" in (record["file"].path or "").replace("\\", "/")


def _get_log_broker() -> Any:
    """取 AstrBot 的 LogBroker，拿不到返回 None。"""
    global _log_broker
    if _log_broker is not None:
        return _log_broker
    try:
        from astrbot.core.log import LogManager

        _log_broker = getattr(LogManager, "_log_broker", None)
    except Exception:
        _log_broker = None
    return _log_broker


def _publish_to_webui(record: dict, extra: dict) -> None:
    """把记录发到 LogBroker（WebUI 面板只读它，不经过 loguru sink）。"""
    broker = _get_log_broker()
    if broker is None:
        return
    try:
        level = record["level"].name
        stamp = record["time"].strftime("%Y-%m-%d %H:%M:%S.") + (
            f"{record['time'].microsecond // 1000:03d}"
        )
        line = (
            f"[{stamp}] {extra['plugin_tag']} [{extra['short_levelname']}]"
            f"{extra['astrbot_version_tag']} "
            f"[{extra['source_file']}:{extra['source_line']}]: {record['message']}"
        )
        exc = record["exception"]
        if exc is not None:
            line += "\n" + "".join(
                traceback.format_exception(exc.type, exc.value, exc.traceback)
            )
        color = _LEVEL_COLOR.get(level)
        broker.publish(
            {
                "level": level,
                "time": time.time(),
                "data": f"{color}{line}\u001b[0m" if color else line,
                "category": "system",
            }
        )
    except Exception:
        pass


def _patch_record(record: dict) -> None:
    """只给 hikari-core 的记录补 extra，其余放行。"""
    if _is_hikari_record(record):
        extra = record["extra"]
        level = record["level"]
        extra.setdefault("plugin_tag", PLUGIN_TAG)
        extra.setdefault("short_levelname", _short_level_name(level.name))
        extra.setdefault(
            "astrbot_version_tag",
            f" [v{_VERSION}]" if _VERSION and level.no >= 30 else "",
        )
        extra.setdefault("source_file", _source_file(record["file"].path))
        extra.setdefault("source_line", record["line"])
        extra.setdefault("is_trace", False)
        extra.setdefault("category", "system")
        _publish_to_webui(record, extra)

    if _previous_patcher is not None:
        _previous_patcher(record)


def install() -> None:
    """安装 core 级 patcher（幂等）。"""
    global _installed, _previous_patcher
    if _installed:
        return
    _installed = True

    core = getattr(_loguru, "_core", None)
    _previous_patcher = getattr(core, "patcher", None) if core is not None else None
    _loguru.configure(patcher=_patch_record)
