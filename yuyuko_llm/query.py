"""LLM 工具的查询编排层。

把「hikari-core 查询 -> 提取精简字段 -> 渲染发图」串起来，并负责：
把工具参数拼成 hikari 认识的指令文本、判断该不该渲染、格式化候选列表。

▍为什么用 init_hikari_no_output 而不是 init_hikari
``init_hikari`` = ``init_hikari_no_output`` + ``output_hikari``，而 ``output_hikari``
会把 ``Output.Data`` **就地替换**成 HTML 字符串、再替换成图片 bytes。
图片对语言模型毫无价值，而原始 dict 才是提取字段的输入 ——
所以这里先走 ``no_output`` 拿数据，提取完再单独调用 ``output_hikari`` 渲染给用户看。
一份数据两用，且不需要改 hikari-core。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from hikari_core import Hikari_Model, callback_hikari, init_hikari_no_output, output_hikari

from .extract import BattleStatsExtractor, normalize_battle_mode
from .guide import classify_error

__all__ = [
    "QueryRunner",
    "QueryResult",
    "build_account_command",
    "build_ship_command",
    "build_recent_command",
    "build_ships_command",
    "build_bind_command",
    "build_roll_command",
]


# ===========================================================================
# 指令文本拼装
#
# hikari 的指令解析口径（commands/parser.py）：
#   - `me` 或缺省 -> 查自己（自动落到该用户已绑定的账号）
#   - `<服务器> <昵称> <指令>` -> 查别人（顺序不限，服务器名走关键字匹配）
# 所以「查自己」和「查别人」只需要决定要不要把 server/nickname 拼进去。
# ===========================================================================

# 服务器名规范写法（与 hikari constants.servers 的关键字一致）
ASTRBOT_SERVER_ALIASES = {
    "亚服": "亚服", "asia": "亚服",
    "国服": "国服", "cn": "国服", "china": "国服",
    "欧服": "欧服", "eu": "欧服", "europe": "欧服",
    "美服": "美服", "na": "美服", "us": "美服",
    "俄服": "俄服", "ru": "俄服", "russia": "俄服",
}


def normalize_server(text: str | None) -> str | None:
    """把服务器说法归一到 hikari 认识的关键字；识别不了就原样返回。"""
    if not text:
        return None
    key = str(text).strip().lower()
    return ASTRBOT_SERVER_ALIASES.get(key) or str(text).strip()


def _identity_tokens(server: str | None, nickname: str | None) -> list[str]:
    """查别人时的「服务器 + 昵称」前缀；没给昵称就返回空（= 查自己）。

    ``server`` 可选：只给昵称也能查（hikari 会按昵称在所有服务器里找或要求补服务器），
    所以这里不强制两个都给。
    """
    tokens: list[str] = []
    if nickname:
        if server:
            tokens.append(normalize_server(server) or server)
        tokens.append(str(nickname).strip())
    return tokens


def build_account_command(server: str | None = None, nickname: str | None = None) -> str:
    """查总表：``me`` / ``<服务器> <昵称>``。

    ▍为什么查自己必须显式带上 ``me``
    hikari 的指令路由要靠 ``_is_identity_query`` 识别「无指令关键词的身份查询」，
    而它对**空列表返回 False**（见 ``commands/router.py``）——
    也就是说空命令会被判成「未识别的指令，请发送 wws help」，
    根本走不到总表逻辑。所以查自己时必须显式给 ``me``。
    """
    tokens = _identity_tokens(server, nickname)
    return " ".join(tokens) if tokens else "me"


def build_ship_command(
    ship: str,
    server: str | None = None,
    nickname: str | None = None,
) -> str:
    """查单船：``单船 <船名>`` / ``<服务器> <昵称> 单船 <船名>``。"""
    return " ".join([*_identity_tokens(server, nickname), "ship", str(ship)])


def build_recent_command(
    days: int | None = None,
    server: str | None = None,
    nickname: str | None = None,
    mode: str = "recent",
) -> str:
    """查近期战绩。

    Args:
        mode: hikari 的指令词 —— ``recent``（随机+排位）/ ``recent_random``（仅随机）
            / ``recent_rank``（仅排位）。
    """
    parts = [*_identity_tokens(server, nickname), mode]
    if days:
        parts.append(str(days))
    return " ".join(parts)


def build_ship_recent_command(
    ship: str,
    days: int | None = None,
    server: str | None = None,
    nickname: str | None = None,
) -> str:
    """查单船近期：``ship.recent <船名> [天数]``。

    ``ship.recent`` 必须排在 ``ship`` 之前（hikari 的指令表有说明），
    这里直接给完整指令词，不做字符串替换 —— 船名里恰好含 ``ship`` 也不会被改坏。
    """
    parts = [*_identity_tokens(server, nickname), "ship.recent", str(ship)]
    if days:
        parts.append(str(days))
    return " ".join(parts)


def build_ships_command(
    min_level: int | None = None,
    max_level: int | None = None,
    nation: str | None = None,
    ship_type: str | None = None,
    server: str | None = None,
    nickname: str | None = None,
) -> str:
    """筛选查询：``ships <国家> <类型> min N max M``（顺序不限，缺省项不拼）。"""
    parts = [*(_identity_tokens(server, nickname)), "ships"]
    if nation:
        parts.append(str(nation))
    if ship_type:
        parts.append(str(ship_type))
    if min_level is not None:
        parts += ["min", str(min_level)]
    if max_level is not None:
        parts += ["max", str(max_level)]
    return " ".join(parts)


def build_bind_command(server: str, nickname: str) -> str:
    """绑定账号：``bind <服务器> <昵称>``（两个都必填，否则 hikari 会报参数错误）。"""
    return " ".join(["bind", normalize_server(server) or server, str(nickname)])


def build_roll_command(
    nation: str | None = None,
    ship_type: str | None = None,
    level: int | None = None,
) -> str:
    """随机战舰：``roll [国家] [类型] [等级]``，都不给就是全随机。"""
    parts = ["roll"]
    if nation:
        parts.append(str(nation))
    if ship_type:
        parts.append(str(ship_type))
    if level is not None:
        parts.append(str(level))
    return " ".join(parts)


# 命令字面量映射：固定参数的查询直接查表，省得在 main.py 里散落魔法字符串。
# 需要拼参数（身份 / 天数 / 筛选条件）的用上面的函数。
COMMAND_BUILDERS: dict[str, str] = {
    "recent_random": "recent_random",
    "recent_rank": "recent_rank",
    "recents": "recents",
    "bind_list": "bind_list",
    "bind_change": "change_bind",
}


def build_literal_command(
    key: str,
    server: str | None = None,
    nickname: str | None = None,
) -> str:
    """无额外参数的查询：``<服务器> <昵称> <指令>``。"""
    return " ".join(
        part for part in (*_identity_tokens(server, nickname), COMMAND_BUILDERS[key]) if part
    )


# ===========================================================================
# 查询执行
# ===========================================================================


@dataclass
class QueryResult:
    """一次工具查询的结果。

    Attributes:
        ok: 查询是否成功拿到数据
        data: 给 LLM 的精简字段（成功时）
        message: 给 LLM 的错误/提示文本（失败或需引导时）
        hikari: 原始 Hikari_Model，调用方据此渲染发图
        select_options: 需要用户二次选择时的候选列表
        need_select: 是否在等选择
        error_kind: 故障分类（network / exception），仅异常路径会有值
    """

    ok: bool = False
    data: Any = field(default_factory=dict)
    message: str = ""
    hikari: Hikari_Model | None = None
    select_options: list = field(default_factory=list)
    need_select: bool = False
    error_kind: str = ""


class QueryRunner:
    """执行一次 hikari 查询并按查询类型提取字段。"""

    def __init__(self, platform: str, platform_id: str, bot_id: str, group_id: str | None = None):
        self.platform = platform
        self.platform_id = platform_id
        self.bot_id = bot_id
        self.group_id = group_id

    async def run(
        self,
        command_text: str,
        extractor: str,
        *,
        select_index: int | None = None,
        battle_mode: str | None = None,
        **extract_kwargs: Any,
    ) -> QueryResult:
        """执行查询。

        Args:
            command_text: 拼好的 hikari 指令文本（不带 ``wws``）
            extractor: 用哪个提取方法，见 ``_EXTRACTORS``
            select_index: 二次选择时用户给出的序号
            battle_mode: 只要某个战斗模式（None = 全部，即默认行为）
            extract_kwargs: 透传给提取方法的额外参数
        """
        result = QueryResult()
        try:
            # select_index 是「上一轮返回了候选项，这一轮带序号继续」的分支
            if select_index is not None:
                hikari = await self._run_with_select(command_text, select_index)
            else:
                hikari = await init_hikari_no_output(
                    command_text=command_text,
                    platform=self.platform,
                    PlatformId=self.platform_id,
                    BotId=self.bot_id,
                    GroupId=self.group_id,
                )
            result.hikari = hikari

            if hikari.Status == 'wait':
                # 需要用户挑一个：把候选项交给 LLM 转述，不在这里阻塞等待
                result.need_select = True
                result.select_options = hikari.Input.Select_Data or []
                result.message = format_select_options(result.select_options)
                return result

            if hikari.Status != 'success':
                # error / failed：Output.Data 就是给用户看的错误文案
                result.message = str(hikari.Output.Data)
                return result

            raw = hikari.Output.Data
            mode = normalize_battle_mode(battle_mode)
            result.data = self._extract(extractor, raw, only=mode, **extract_kwargs)
            result.ok = True
            return result
        except Exception as e:
            logger.exception(f"LLM 工具查询异常: {e}")
            # 分类交给上层决定话术：网络波动提示重试，程序异常引导加群反馈
            result.ok = False
            result.error_kind = classify_error(e)
            result.message = str(e) or type(e).__name__
            return result

    async def render(self, result: QueryResult) -> bytes | None:
        """把查询结果渲染成图片；不需要渲染或渲染失败返回 None。

        单独一步是因为：提取字段只需要原始 dict，而渲染要等提取完再做
        （``output_hikari`` 会就地替换 ``Output.Data``）。
        """
        hikari = result.hikari
        if hikari is None or not result.ok:
            return None
        try:
            hikari = await output_hikari(hikari)
        except Exception as e:
            # output_hikari 内部通常已兜住异常并转成 error 状态，这里只防空
            logger.warning(f"LLM 工具渲染失败: {e}")
            return None
        if hikari is not result.hikari:
            result.hikari = hikari
        data = hikari.Output.Data
        return data if isinstance(data, bytes) else None

    async def _run_with_select(self, command_text: str, select_index: int) -> Hikari_Model:
        """带序号的二次调用：先重建 wait 状态，再走 callback_hikari。"""
        hikari = await init_hikari_no_output(
            command_text=command_text,
            platform=self.platform,
            PlatformId=self.platform_id,
            BotId=self.bot_id,
            GroupId=self.group_id,
        )
        if hikari.Status != 'wait':
            return hikari
        options = hikari.Input.Select_Data or []
        if not (1 <= int(select_index) <= len(options)):
            return hikari.failed(f"序号超出范围，可选 1-{len(options)}")
        hikari.Input.Select_Index = int(select_index)
        return await callback_hikari(hikari)

    @staticmethod
    def _extract(
        extractor: str, raw: Any, *, only: str | None = None, **kwargs: Any
    ) -> Any:
        """按名字调用提取器；未知名字返回空 dict 而不是抛异常。"""
        ext = BattleStatsExtractor
        if extractor == "account":
            return ext.account_summary(raw)
        if extractor == "ship":
            return ext.ship_summary(raw)
        if extractor == "recent":
            return ext.recent_summary(raw, only=only)
        if extractor == "recents":
            return ext.recents_summary(raw)
        if extractor == "ships":
            return ext.ships_summary(raw)
        if extractor == "roll":
            return ext.roll_summary(raw)
        if extractor == "bind":
            # 绑定类接口的返回有三种：绑定列表 / 「绑定成功」这类消息 / 其它结构，
            # 统一交给 bind_result 归一化（字符串就直接用，列表就压成精简账号表）
            return ext.bind_result(raw)
        return {}


def format_select_options(options: list, limit: int = 20) -> str:
    """把候选项格式化成给 LLM 的带序号列表。

    形状来自 hikari 的 ``Select_Data``：船是 ``{nameCn, shipType, level}``，
    军团是 ``{tag, name}``；两种都做宽松解析。
    """
    if not options:
        return "没有可选项。"
    lines = ["需要用户选择，请把下面的列表转述给用户并让 TA 回复序号："]
    for index, item in enumerate(options[:limit], start=1):
        if isinstance(item, dict):
            name = item.get("nameCn") or item.get("name") or item.get("tag") or "?"
            extra = item.get("shipType") or item.get("tag") or ""
            level = item.get("level")
            detail = " ".join(str(x) for x in (level, extra) if x)
            lines.append(f"  {index}. {name}{('（' + detail + '）') if detail else ''}")
        else:
            lines.append(f"  {index}. {item}")
    if len(options) > limit:
        lines.append(f"  …（共 {len(options)} 项，仅列出前 {limit} 项）")
    lines.append("用户选定后，用同样的参数再次调用本工具并填写 select_index。")
    return "\n".join(lines)
