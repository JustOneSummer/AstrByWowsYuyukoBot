"""面向 LLM 的工具层（function-calling / tools-use）。

把 hikari-core 的战绩查询能力暴露成 AstrBot 的 LLM 工具，让用户可以用自然语言
（"我最近打得怎么样"）而不是指令（``/wws recent``）来查询。

模块分工：

- :mod:`~yuyuko_llm.extract`   返回数据 -> 给模型看的精简字段（加字段只改这里）
- :mod:`~yuyuko_llm.query`     查询编排 + 参数拼成 hikari 指令
- :mod:`~yuyuko_llm.tool_spec` 工具描述文案（模型选工具的依据）
- :mod:`~yuyuko_llm.guide`     开关 / 限流 / 错误话术
- :mod:`~yuyuko_llm.executor`  一次调用的公共流程（查数据 -> 发图 -> 返回文本）

⚠️ ``@llm_tool`` 装饰的函数**不在这个包里**，而是在 ``main.py``：
AstrBot 用 ``ft.handler.__module__ == metadata.module_path`` 判断工具归属
（``astrbot/core/star/star_manager.py``），只有 ``main.py`` 的模块路径等于插件模块路径，
工具才能拿到 ``self``（Star 实例）并出现在面板的启停列表里。

导入顺序提醒：``query`` / ``executor`` 依赖 ``hikari_core``，
调用方需先 ``import yuyuko_bot``（它负责把内置源码挂进 ``sys.path``）。
"""

from .executor import ToolExecutor
from .extract import BattleStatsExtractor, normalize_battle_mode
from .guide import (
    ERROR_EXCEPTION,
    ERROR_NETWORK,
    ToolGuard,
    classify_error,
    config_get_bool,
    humanize_error,
    user_error_text,
)
from .query import (
    COMMAND_BUILDERS,
    QueryResult,
    QueryRunner,
    build_account_command,
    build_bind_command,
    build_literal_command,
    build_recent_command,
    build_roll_command,
    build_ship_command,
    build_ship_recent_command,
    build_ships_command,
)
from .tool_spec import (
    EXCEPTION_GUIDE,
    FEEDBACK_CONTACT,
    IMAGE_FAILED_GUIDE,
    NETWORK_GUIDE,
    SERVER_CODE_HINT,
    USER_EXCEPTION_TEXT,
    USER_NETWORK_TEXT,
)

__all__ = [
    "BattleStatsExtractor",
    "COMMAND_BUILDERS",
    "ERROR_EXCEPTION",
    "ERROR_NETWORK",
    "EXCEPTION_GUIDE",
    "FEEDBACK_CONTACT",
    "IMAGE_FAILED_GUIDE",
    "NETWORK_GUIDE",
    "QueryResult",
    "QueryRunner",
    "SERVER_CODE_HINT",
    "ToolExecutor",
    "ToolGuard",
    "USER_EXCEPTION_TEXT",
    "USER_NETWORK_TEXT",
    "build_account_command",
    "build_bind_command",
    "build_literal_command",
    "build_recent_command",
    "build_roll_command",
    "build_ship_command",
    "build_ship_recent_command",
    "build_ships_command",
    "classify_error",
    "config_get_bool",
    "humanize_error",
    "normalize_battle_mode",
    "user_error_text",
]
