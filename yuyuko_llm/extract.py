"""从 hikari-core 的返回数据里提取「给 LLM 看」的字段。

▍为什么要单独一层
hikari-core 的 ``Output.Data`` 是**渲染给模板用的**结构，里面混着大量只对画面有意义、
对语言模型毫无价值的东西：颜色值（``color`` / ``winsData``）、CSS 渐变串、base64 图、
本地化了路径的图片 URL。整份丢给模型既浪费 token 也干扰理解。

所以这里做**白名单提取**：只按路径取需要的那几个数，其余一律不进 prompt。

▍怎么加字段
只需要改 :attr:`BattleStatsExtractor.BATTLE_FIELDS`（战斗统计）或对应的
``*_FIELDS`` 常量，调用方一行都不用动。
新增一个战斗模式，只需要往 :attr:`BattleStatsExtractor.MODES` 里加一行。

▍为什么 5 个模式能共用一套字段
``battleTypeInfo`` 下的 ``PVP`` / ``PVP_SOLO`` / ``PVP_DIV2`` / ``PVP_DIV3`` /
``RANK_SOLO`` 是**同构**的（模板 wws-info-recent-random-v6.html 里就是复制粘贴的五段），
字段路径完全一致，所以 :meth:`battle_stats` 一份实现即可。

▍容错原则
取值一律走 ``_dig``（逐层 ``.get``），任何一层缺失就返回 ``None``，绝不抛异常 ——
接口加字段/改字段时最坏的结果是少几个数，而不是整个工具报错。
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "BattleStatsExtractor",
    "normalize_battle_mode",
    "MODE_ALIASES",
]


class BattleStatsExtractor:
    """hikari-core 返回数据 -> 精简结构化字段。"""

    # ▍战斗模式：数据键 -> 中文名。
    # 中文名刻意带上「几个人」，因为「自行车 / 三轮车」是国服黑话，
    # 模型（和不少用户）不一定懂，而「2 人组队」是任何模型都能理解的语义。
    MODES: dict[str, str] = {
        "PVP": "随机",
        "PVP_SOLO": "单野（1 人）",
        "PVP_DIV2": "自行车（2 人组队）",
        "PVP_DIV3": "三轮车（3 人组队）",
        "RANK_SOLO": "排位",
    }

    # 按用户口头说法选模式时优先展示的顺序（默认「随机」在前）
    MODE_ORDER: tuple[str, ...] = ("PVP", "PVP_SOLO", "PVP_DIV2", "PVP_DIV3", "RANK_SOLO")

    # ▍战斗统计字段白名单：输出名 -> battleTypeInfo.<模式> 下的字段路径。
    # 加字段就在这里加一行。
    BATTLE_FIELDS: dict[str, tuple[str, ...]] = {
        "battles": ("battleInfo", "battleInfo", "battle"),
        "survived": ("battleInfo", "battleInfo", "survived"),
        "win_rate": ("battleInfo", "avgInfo", "win"),
        "avg_damage": ("battleInfo", "avgInfo", "damage"),
        "avg_frags": ("battleInfo", "avgInfo", "frags"),
        "avg_kd": ("battleInfo", "avgInfo", "kd"),
        "avg_xp": ("battleInfo", "avgInfo", "xp"),
        "hit_ratio": ("hitRatioInfo", "ratioMain"),
        "pr": ("prInfo", "value"),
    }

    # 总表（wws-info-v6.html）用到的顶层节点
    ACCOUNT_FIELDS: dict[str, tuple[str, ...]] = {
        "nickname": ("userInfo", "nickName"),
        "account_id": ("userInfo", "accountId"),
        "server": ("userInfo", "serverName"),
        "clan": ("userInfo", "clanInfo", "clanName"),
        "last_battle_time": ("lastBattleTime",),
        "pr": ("prInfo", "value"),
        "level": ("levelInfo", "level"),
    }

    # 单船（wws-info-recent-v6.html 的 shipInfo 节点）
    SHIP_FIELDS: dict[str, tuple[str, ...]] = {
        "ship_name": ("shipInfo", "nameCn"),
        "ship_name_en": ("shipInfo", "nameEnglish"),
        "ship_type": ("shipInfo", "shipType"),
        "ship_level": ("shipInfo", "level"),
        "ship_nation": ("shipInfo", "country"),
    }

    # ---------------------------------------------------------------- 基础取值

    @staticmethod
    def _dig(data: Any, path: Iterable[str]) -> Any:
        """按路径逐层取值，任何一层缺失返回 None。"""
        cur = data
        for key in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
            if cur is None:
                return None
        return cur

    @classmethod
    def _pick(cls, data: Any, fields: dict[str, tuple[str, ...]]) -> dict[str, Any]:
        """按字段白名单提取，自动丢掉取不到的项。"""
        out: dict[str, Any] = {}
        for name, path in fields.items():
            value = cls._dig(data, path)
            if value is not None:
                out[name] = value
        return out

    @staticmethod
    def _round(value: Any, digits: int = 2) -> Any:
        """数值压到指定小数位；非数值原样返回。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return round(value, digits)
        return value

    # ---------------------------------------------------------------- 战斗统计

    # ▍各模式统计所在节点的候选键名。
    # **这不是冗余**：不同接口用的键不一样 ——
    #   近期 / 总表（wws-info-*.html）用 ``battleTypeInfo``
    #   单船（wws-ship-v6.html）、筛选列表里的每条船用 ``typeInfo``
    # 只认一个键会让另一边的模式数据读成空（表现为「模型说没有数据」）。
    BATTLE_NODE_KEYS: tuple[str, ...] = ("battleTypeInfo", "typeInfo")

    @classmethod
    def _battle_node(cls, data: Any) -> dict:
        """取出承载各模式统计的节点（兼容 battleTypeInfo / typeInfo）。"""
        if not isinstance(data, dict):
            return {}
        for key in cls.BATTLE_NODE_KEYS:
            node = data.get(key)
            if isinstance(node, dict) and node:
                return node
        return {}

    @classmethod
    def battle_modes_present(cls, data: Any) -> list[str]:
        """数据里实际有场次的模式（按 MODE_ORDER 排序）。

        「有场次」= ``<节点>.<模式>.battle`` 大于 0；模板也是靠这个值决定要不要渲染。
        """
        info = cls._battle_node(data)
        if not info:
            return []
        present = [
            mode
            for mode in cls.MODE_ORDER
            if isinstance(info.get(mode), dict) and (info[mode].get("battle") or 0) > 0
        ]
        # 数据里出现了 MODES 没登记的模式也一并带上，避免上游加模式就静默丢数据
        for mode, payload in info.items():
            if mode in present or mode in cls.MODES:
                continue
            if isinstance(payload, dict) and (payload.get("battle") or 0) > 0:
                present.append(mode)
        return present

    @classmethod
    def battle_stats(cls, data: Any, mode: str) -> dict[str, Any] | None:
        """单个模式的统计数据；该模式没有场次时返回 None。"""
        payload = cls._battle_node(data).get(mode)
        if not isinstance(payload, dict):
            return None
        if (payload.get("battle") or 0) <= 0:
            return None
        stats = cls._pick(payload, cls.BATTLE_FIELDS)
        for key in ("win_rate", "avg_damage", "avg_frags", "avg_kd", "avg_xp", "hit_ratio"):
            if key in stats:
                stats[key] = cls._round(stats[key])
        stats["mode"] = mode
        stats["mode_label"] = cls.MODES.get(mode, mode)
        return stats

    @classmethod
    def battle_types(cls, data: Any, only: str | None = None) -> dict[str, Any]:
        """各模式统计（近期 / 总表 / 单船共用）。

        Args:
            data: hikari 的 ``Output.Data``（节点名 battleTypeInfo 或 typeInfo 都认）
            only: 只要这一个模式（None = 全部有场次的模式，即「默认全给」）
        """
        if only:
            stats = cls.battle_stats(data, only)
            return {only: stats} if stats else {}
        return {
            mode: stats
            for mode in cls.battle_modes_present(data)
            if (stats := cls.battle_stats(data, mode)) is not None
        }

    # ---------------------------------------------------------------- 各查询摘要

    @classmethod
    def account_summary(cls, data: Any) -> dict[str, Any]:
        """总表：账号基本信息 + 各模式统计。"""
        summary = cls._pick(data, cls.ACCOUNT_FIELDS)
        summary["battle_types"] = cls.battle_types(data)
        # 船型分布 / 等级分布对「评价玩家风格」有用，只取名称与场次，不取图片与颜色
        summary["ship_types"] = cls._named_counts(data, "shipTypeInfo")
        summary["levels"] = cls._named_counts(data, "levelInfo")
        return summary

    @classmethod
    def recent_summary(cls, data: Any, only: str | None = None) -> dict[str, Any]:
        """近期战绩：账号信息 + 指定/全部模式统计 + 逐船明细。"""
        summary = cls._pick(data, cls.ACCOUNT_FIELDS)
        summary["battle_types"] = cls.battle_types(data, only=only)
        ships = cls.recent_ships(data)
        if ships:
            summary["ships"] = ships
        return summary

    @classmethod
    def recent_ships(cls, data: Any, limit: int = 20) -> list[dict[str, Any]]:
        """近期用过的船（wws-info-recent-random-v6.html 里的逐船表）。

        每艘船带 ``typeInfo``（各模式统计），这里只取 ``PVP`` 一节做代表，
        并按场次降序取前 ``limit`` 艘，避免船多时把 prompt 撑爆。
        """
        ships = cls._dig(data, ("shipInfo",))
        if not isinstance(ships, list):
            return []
        rows: list[dict[str, Any]] = []
        for ship in ships:
            if not isinstance(ship, dict):
                continue
            stats = cls.battle_stats({"battleTypeInfo": ship.get("typeInfo") or {}}, "PVP")
            if not stats:
                continue
            row = cls._pick(ship, {
                "ship_name": ("nameCn",),
                "ship_name_en": ("nameEnglish",),
                "ship_type": ("shipType",),
                "ship_level": ("level",),
            })
            row["battles"] = stats.get("battles")
            row["win_rate"] = stats.get("win_rate")
            row["avg_damage"] = stats.get("avg_damage")
            row["avg_frags"] = stats.get("avg_frags")
            row["pr"] = stats.get("pr")
            rows.append(row)
        rows.sort(key=lambda r: r.get("battles") or 0, reverse=True)
        return rows[:limit]

    @classmethod
    def ship_summary(cls, data: Any) -> dict[str, Any]:
        """单船：船只信息 + 各模式统计。"""
        summary = cls._pick(data, cls.SHIP_FIELDS)
        summary["battle_types"] = cls.battle_types(data)
        return summary

    @classmethod
    def roll_summary(cls, data: Any) -> dict[str, Any]:
        """随机战舰：只取被抽中的那条船的信息。"""
        return cls._pick(data, cls.SHIP_FIELDS)

    # ---------------------------------------------------------------- 筛选 / 单场

    @classmethod
    def ships_summary(cls, data: Any, limit: int = 25) -> dict[str, Any]:
        """筛选查询（wws ships）：筛选条件 + 命中的船列表。

        模板 wws-ships-v6.html 的节点是 ``filter`` / ``list`` / ``typeInfo``。
        每条记录的字段名按上游口径做**宽松解析**（见 :meth:`_ship_row`），
        拿不准的字段缺失时跳过，不会抛异常。
        """
        summary = cls._pick(data, cls.ACCOUNT_FIELDS)
        summary["filter"] = data.get("filter") if isinstance(data, dict) else None
        rows = []
        for item in cls._as_list(data, ("list",)):
            row = cls._ship_row(item)
            if row:
                rows.append(row)
        rows.sort(key=lambda r: r.get("battles") or 0, reverse=True)
        summary["ships"] = rows[:limit]
        summary["ship_count"] = len(rows)
        return summary

    @classmethod
    def recents_summary(cls, data: Any, limit: int = 20) -> dict[str, Any]:
        """单场近期（wws recents）：逐场明细 + 按船汇总。

        模板 wws-info-recents-v6.html 用 ``shipInfos``（逐场，带 ``recordTime``）
        和 ``shipnfosTotal``（按船汇总）。逐场取最近 ``limit`` 场。
        """
        summary = cls._pick(data, cls.ACCOUNT_FIELDS)
        if isinstance(data, dict) and "activate" in data:
            summary["activate"] = data.get("activate")
        recent = cls._as_list(data, ("shipInfos",))
        rows = [cls._ship_row(item) for item in recent]
        summary["recent_battles"] = [r for r in rows if r][:limit]
        total = cls._as_list(data, ("shipnfosTotal", "shipInfosTotal"))
        summary["by_ship"] = [r for r in (cls._ship_row(i) for i in total) if r][:limit]
        return summary

    @staticmethod
    def _as_list(data: Any, keys: Iterable[str]) -> list:
        """按候选键名取列表；取不到返回空列表（不同接口口径不一致时容错）。"""
        if not isinstance(data, dict):
            return []
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
        return []

    # 记录里各输出字段的候选键名（不同接口口径不完全统一，逐个试）
    ROW_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
        "ship_name": ("nameCn", "shipName", "name"),
        "ship_name_en": ("nameEnglish", "shipNameEn"),
        "ship_type": ("shipType", "type"),
        "level": ("level", "shipLevel"),
        "nation": ("country", "nation"),
        "result": ("result", "win", "isWin"),
        "record_time": ("recordTime", "battleTime"),
    }

    # 统计字段摊平在记录里时的候选键名（多数情况下在 typeInfo 里）
    ROW_STAT_CANDIDATES: dict[str, tuple[str, ...]] = {
        "battles": ("battles", "battle"),
        "win_rate": ("winRate", "win"),
        "avg_damage": ("damage", "avgDamage"),
        "avg_frags": ("frags", "avgFrags"),
        "survived": ("survived",),
        "pr": ("pr",),
    }

    @staticmethod
    def _pick_loose(item: Any, candidates: tuple[str, ...]) -> Any:
        """在候选键名里取第一个有值的（宽松解析，用于口径不统一的接口）。"""
        if not isinstance(item, dict):
            return None
        for key in candidates:
            value = item.get(key)
            if value not in (None, "", []):
                return value
        return None

    @classmethod
    def _ship_row(cls, item: Any) -> dict[str, Any]:
        """把一条「船 / 单场」记录压成精简行。

        统计优先从嵌套的 ``typeInfo`` / ``battleTypeInfo`` 取（与近期模板一致），
        取不到再退回摊平在记录自身上的字段。
        """
        row: dict[str, Any] = {}
        for out_key, candidates in cls.ROW_FIELD_CANDIDATES.items():
            value = cls._pick_loose(item, candidates)
            if value is not None:
                row[out_key] = value

        # 统计优先从嵌套节点取（typeInfo / battleTypeInfo 都认），
        # 取不到再退回摊平在记录自身上的字段
        stats: dict[str, Any] = {}
        if isinstance(item, dict):
            stats = cls.battle_stats(item, "PVP") or {}
        if not stats:
            stats = cls._pick(item, cls.BATTLE_FIELDS)
        for key, candidates in cls.ROW_STAT_CANDIDATES.items():
            value = stats.get(key)
            if value is None:
                value = cls._pick_loose(item, candidates)
            if value is not None:
                row[key] = value if key == "battles" else cls._round(value)
        # 整行都是空的就没必要占用 prompt
        return row if any(k in row for k in cls.ROW_FIELD_CANDIDATES) else {}

    @staticmethod
    def _named_counts(data: Any, node: str, limit: int = 12) -> list[dict[str, Any]]:
        """把 ``shipTypeInfo`` / ``levelInfo`` 这类「名称 + 场次」分布压成列表。

        结构在不同接口里不完全一致，这里做成宽松解析：只要是
        ``{'name': ..., 'battle': ...}`` 形状的条目就收，取不到的字段跳过。
        """
        payload = data.get(node) if isinstance(data, dict) else None
        rows: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                if not isinstance(value, dict):
                    continue
                rows.append({
                    "name": value.get("nameCn") or value.get("name") or key,
                    "battles": value.get("battle") or value.get("battles"),
                })
        elif isinstance(payload, list):
            for value in payload:
                if not isinstance(value, dict):
                    continue
                rows.append({
                    "name": value.get("nameCn") or value.get("name") or value.get("type"),
                    "battles": value.get("battle") or value.get("battles"),
                })
        rows = [r for r in rows if r.get("name")]
        rows.sort(key=lambda r: r.get("battles") or 0, reverse=True)
        return rows[:limit]

    # ---------------------------------------------------------------- 账号绑定

    @classmethod
    def bind_list_summary(cls, data: Any) -> dict[str, Any]:
        """绑定列表：把上游结构压成模型能直接用的精简列表。

        上游每条记录的字段是 ``{accountId, userName, server, defaultAccount}``
        （见 ``features/bind.py`` 的 ``get_BindInfo``，模板 bind-list-v6.html 也是读这几个）。

        ▍为什么要在这里加工，而不是原样丢给模型
        - **序号**：``change_bind`` / ``delete_bind`` 都按序号取账号
          （``Select_Data[Select_Index-1]['accountId']``），而序号只存在于**列表顺序**里，
          记录本身没有这个字段。所以必须显式生成，模型才知道该回哪个序号。
        - **服务器**：上游给的是 ``asia`` / ``cn`` 这类代码，模板靠前端 JS 才显示成中文，
          Python 侧拿到的是代码。不转成中文，模型转述给用户时就只能说 "asia"。
        - **当前账号**：靠 ``defaultAccount == accountId`` 判定（模板同款逻辑），
          模型据此回答「我现在绑的是哪个」。
        """
        accounts = []
        for index, item in enumerate(cls._bind_items(data), start=1):
            if not isinstance(item, dict):
                continue
            account_id = cls._pick_loose(item, ("accountId", "aid", "id"))
            default_mark = cls._pick_loose(item, ("defaultAccount", "defaultId", "default"))
            row: dict[str, Any] = {"index": index}
            if account_id is not None:
                row["account_id"] = account_id
            name = cls._pick_loose(item, ("userName", "nickName", "name"))
            if name is not None:
                row["user_name"] = name
            server = cls._pick_loose(item, ("server", "serverName"))
            if server is not None:
                row["server"] = server
                row["server_cn"] = _server_cn(server)
            # defaultAccount 与 accountId 相等即为当前绑定账号（模板同款判定）
            is_current = bool(
                default_mark is not None
                and account_id is not None
                and str(default_mark) == str(account_id)
            )
            # defaultId 是布尔标记时的兼容分支
            if not is_current and isinstance(default_mark, bool) and default_mark:
                is_current = True
            row["is_current"] = is_current
            accounts.append(row)
        return {"count": len(accounts), "accounts": accounts}

    @staticmethod
    def _bind_items(data: Any) -> list:
        """取出绑定记录列表；兼容「裸列表」与「包了一层键」两种返回形状。"""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("bindList", "data", "list", "records"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    @classmethod
    def bind_result(cls, data: Any) -> Any:
        """绑定 / 切换 / 删除的返回：可能是字符串消息，也可能是绑定列表。

        - 字符串（"绑定成功" / "切换绑定成功，当前绑定账号…"）→ 直接给模型这句话
        - 列表 / 字典 → 走 :meth:`bind_list_summary`
        """
        if isinstance(data, str):
            return data
        if isinstance(data, (list, dict)):
            return cls.bind_list_summary(data)
        return data


# 服务器代码 -> 中文（与模板 bind-list-v6.html 里那段 JS 的映射保持一致）
_SERVER_CN = {
    "asia": "亚服",
    "cn": "国服",
    "eu": "欧服",
    "na": "美服",
    "ru": "俄服",
}


def _server_cn(server: Any) -> str:
    """把服务器代码转成中文；已经是中文（或认不出来）就原样返回。"""
    text = str(server).strip()
    return _SERVER_CN.get(text.lower(), text)


# ---------------------------------------------------------------------------
# 模式别名容错
# ---------------------------------------------------------------------------
# ▍为什么要有别名表
# ``@llm_tool`` 装饰器生成 schema 时只支持 type / name / description 三个字段
# （见 astrbot/core/star/register/star_handler.py 的 register_llm_tool），
# **没有 enum**，没法在协议层限制取值。所以模型可能填「单野」「单人」「单排」
# 「solo」「自行车」「双排」等任意说法，这里统一归一到数据键。
MODE_ALIASES: dict[str, str] = {
    # 全部随机
    "随机": "PVP", "全部": "PVP", "全部随机": "PVP", "随": "PVP",
    "pvp": "PVP", "all": "PVP", "random": "PVP", "总体": "PVP", "总集": "PVP",
    # 单野（1 人）
    "单野": "PVP_SOLO", "单人": "PVP_SOLO", "单排": "PVP_SOLO", "单飞": "PVP_SOLO",
    "一个人": "PVP_SOLO", "自己一个人": "PVP_SOLO", "solo": "PVP_SOLO",
    "pvp_solo": "PVP_SOLO", "1人": "PVP_SOLO", "一人": "PVP_SOLO",
    # 自行车（2 人）
    "自行车": "PVP_DIV2", "双人": "PVP_DIV2", "双排": "PVP_DIV2", "两人": "PVP_DIV2",
    "2人": "PVP_DIV2", "二人": "PVP_DIV2", "双野": "PVP_DIV2", "双车": "PVP_DIV2",
    "pvp_div2": "PVP_DIV2", "div2": "PVP_DIV2", "组队2": "PVP_DIV2",
    # 三轮车（3 人）
    "三轮车": "PVP_DIV3", "三人": "PVP_DIV3", "三排": "PVP_DIV3", "三人车": "PVP_DIV3",
    "3人": "PVP_DIV3", "三野": "PVP_DIV3", "三车": "PVP_DIV3",
    "pvp_div3": "PVP_DIV3", "div3": "PVP_DIV3", "组队3": "PVP_DIV3",
    # 排位
    "排位": "RANK_SOLO", "竞技": "RANK_SOLO", "rank": "RANK_SOLO",
    "ranked": "RANK_SOLO", "rank_solo": "RANK_SOLO", "排位战": "RANK_SOLO",
}


def normalize_battle_mode(text: str | None) -> str | None:
    """把用户/模型给的模式说法归一到数据键；无法识别时返回 None。

    ``None`` / 空串表示「不指定」= 默认全部模式。
    """
    if not text:
        return None
    key = str(text).strip().lower().replace(" ", "").replace("（", "(").replace("）", ")")
    if not key:
        return None
    if key in MODE_ALIASES:
        return MODE_ALIASES[key]
    # 「单野(1人)」这种带括号说明的写法：去掉括号内容再试一次
    stripped = key.split("(")[0]
    if stripped and stripped in MODE_ALIASES:
        return MODE_ALIASES[stripped]
    # 直接就是数据键（大小写不敏感）
    upper = key.upper()
    if upper in BattleStatsExtractor.MODES:
        return upper
    return None
