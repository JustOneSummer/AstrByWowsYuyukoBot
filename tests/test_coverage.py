#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提取器字段覆盖测试（离线，不调 API）。

数据形状来自 ``tools/fixtures/``，那是按真实响应构造的 —— 之前几次踩坑
（单船读空、maxInfo 丢失、路径全错）都是因为测试数据是猜的。

    python tools/test_coverage.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from fixtures import (  # noqa: E402
    account_fixture,
    mode_node,
    recent_fixture,
    recents_fixture,
    roll_fixture,
    ship_fixture,
    ships_list_fixture,
)
from yuyuko_llm import BattleStatsExtractor as E  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  [OK] {label}")
    else:
        print(f"  [!!] {label}")
        FAILS.append(label)


def _flat_keys(obj) -> set[str]:
    """递归收集所有字典键（用于确认某个字段名出现在结果里）。"""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _flat_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            keys |= _flat_keys(v)
    return keys


print("=" * 72)
print("1) 单模式统计：模板读的字段是否都在")
print("=" * 72)
st = E.battle_stats({"battleTypeInfo": {"PVP": mode_node()}}, "PVP")
print(json.dumps(st, ensure_ascii=False, indent=2)[:1800])

need = [
    "battles", "wins", "losses", "survived", "win_and_survived",
    "win_rate", "avg_damage", "avg_frags", "avg_kd", "avg_xp",
    "avg_basic_xp", "avg_scouting_damage", "avg_planes_killed",
    "avg_ships_spotted", "avg_art_agro", "avg_tpd_agro",
    "hit_ratio", "hit_ratio_atba", "hit_ratio_tpd", "hit_ratio_tbomb",
    "capture_contribution", "defense_contribution", "top_grade",
    "last_battle_time", "pr", "pr_next_value", "pr_name",
    "maxFrags", "maxFragsByMain", "maxDamageDealt", "maxScoutingDamage",
    "maxPlanesKilled", "maxShipsSpotted", "maxTotalAgro", "maxXp",
    "fragsByMain", "fragsByTpd", "fragsByPlanes", "fragsByRam",
    "fragsByDbomb", "fragsByAtba",
]
missing = [k for k in need if k not in st]
check(not missing, f"模板字段齐全（缺 {missing}）" if missing else "模板字段齐全")

print()
check(st.get("battles") == 300, f"battles = {st.get('battles')}")
check(abs((st.get("win_rate") or 0) - 55.5) < 1e-6, f"win_rate = {st.get('win_rate')}")
check(st.get("pr") == 1520, f"pr = {st.get('pr')}")
check(st.get("maxDamageDealt") == 210000,
      f"maxDamageDealt = {st.get('maxDamageDealt')}")  # {shipId, value} 包装也要能取出
check(st.get("hit_ratio") == 34.48, f"hit_ratio = {st.get('hit_ratio')}")
check(st.get("top_grade") == 5, f"top_grade = {st.get('top_grade')}")

print()
flat = _flat_keys(st)
noise = [k for k in ("color", "winsData", "damageData", "code", "shipId", "type")
         if k in flat]
check(not noise, f"噪声/渲染字段已过滤（残留 {noise}）" if noise else "噪声/渲染字段已过滤")
redundant = [k for k in ("win", "damage", "frags", "kd", "xp", "ratioMain", "value", "name", "nextValue")
             if k in flat]
check(not redundant, f"同义键已去重（残留 {redundant}）" if redundant else "同义键已去重")

print()
print("=" * 72)
print("2) 单船：shipInfo（船只信息）+ typeInfo（统计）")
print("=" * 72)
s = E.ship_summary(ship_fixture())
for k in ("ship_name", "ship_name_en", "ship_type", "ship_level", "ship_nation", "ship_img"):
    check(bool(s.get(k)), f"{k} = {s.get(k)}")
check(s.get("ship_level") == "X", "等级取的是 levelStr（X）")
bt = s.get("battle_types") or {}
check(set(bt) == {"PVP", "RANK_SOLO"}, f"模式 = {list(bt)}")
check((bt.get("PVP") or {}).get("win_rate") == 58.3, f"PVP 胜率 = {(bt.get('PVP') or {}).get('win_rate')}")
check((bt.get("PVP") or {}).get("maxDamageDealt") == 210000, "单船也能取到最高伤害")

print()
print("=" * 72)
print("3) 总表：账号信息 + 分布（船型中文 / 等级键）")
print("=" * 72)
a = E.account_summary(account_fixture())
check(a.get("nickname") == "Nahida_official", f"nickname = {a.get('nickname')}（真实键是 userName）")
check(a.get("account_id") == 2022515210, f"account_id = {a.get('account_id')}")
check(a.get("server_cn") == "亚服", f"server_cn = {a.get('server_cn')}")
check(a.get("clan_tag") == "YU_RI", f"clan_tag = {a.get('clan_tag')}")
check(a.get("pr_status") == 0, f"pr_status = {a.get('pr_status')}")
check(a.get("pr") == 1868, f"pr = {a.get('pr')}（顶层 prInfo）")
check(len(a.get("battle_types") or {}) == 5, f"模式数 = {len(a.get('battle_types') or {})}")
names = [r["name"] for r in (a.get("ship_types") or [])]
check(set(names) == {"战列舰", "巡洋舰", "驱逐舰"}, f"船型中文 = {names}")
lv = [r["name"] for r in (a.get("levels") or [])]
check(set(lv) == {"10", "9"}, f"等级键 = {lv}")
check(all(r.get("win_rate") is not None for r in (a.get("ship_types") or [])), "分布含胜率")
check(all(r.get("pr") is not None for r in (a.get("ship_types") or [])), "分布含 PR")

print()
print("=" * 72)
print("4) 近期：battleTypeInfo + shipInfoBattleList（逐船）")
print("=" * 72)
r = E.recent_summary(recent_fixture())
check(set(r.get("battle_types") or {}) == {"PVP", "PVP_SOLO"},
      f"模式 = {list(r.get('battle_types') or {})}")
ships = r.get("ships") or []
check(len(ships) == 2, f"逐船条数 = {len(ships)}")
check(ships and ships[0].get("ship_name") == "岛风",
      f"按场次降序，第一条 = {ships[0].get('ship_name') if ships else None}")
check(ships and ships[0].get("level") == "X", f"逐船等级 = {ships[0].get('level') if ships else None}")
check(ships and ships[0].get("battles") == 80, f"逐船场次 = {ships[0].get('battles') if ships else None}")

print()
print("=" * 72)
print("5) 单场近期：shipInfos（逐场） + shipnfosTotal（按船，上游少个 i）")
print("=" * 72)
rr = E.recents_summary(recents_fixture())
rb = rr.get("recent_battles") or []
bs = rr.get("by_ship") or []
check(len(rb) == 2, f"逐场条数 = {len(rb)}")
check(rb and rb[0].get("ship_name") == "岛风", f"逐场第一条 = {rb[0].get('ship_name') if rb else None}")
check(rb and rb[0].get("record_time") == 1790807949374, "逐场带 recordTime")
check(len(bs) == 1, f"按船汇总条数 = {len(bs)}")
check(rr.get("activate") == 1790807949374, f"activate = {rr.get('activate')}")
check(bool(rr.get("battle_types")), "单场近期也带模式统计")

print()
print("=" * 72)
print("6) 筛选：filter + list")
print("=" * 72)
sl = E.ships_summary(ships_list_fixture())
check((sl.get("filter") or {}).get("desc") == "10 级战列舰", f"filter.desc = {(sl.get('filter') or {}).get('desc')}")
rows = sl.get("ships") or []
check(len(rows) == 2, f"船条数 = {len(rows)}")
check(rows and rows[0].get("ship_name") == "大和", f"按场次降序 = {rows[0].get('ship_name') if rows else None}")
check(sl.get("ship_count") == 2, f"ship_count = {sl.get('ship_count')}")

print()
print("=" * 72)
print("7) 随机战舰")
print("=" * 72)
roll = E.roll_summary(roll_fixture())
check(roll.get("ship_name") == "岛风", f"ship_name = {roll.get('ship_name')}")
check(roll.get("ship_level") == "X", f"ship_level = {roll.get('ship_level')}")

print()
print("=" * 72)
print("8) 容错：空 / 畸形数据不抛异常")
print("=" * 72)
for bad in (None, {}, [], {"battleTypeInfo": None}, {"battleTypeInfo": {"PVP": {}}},
            {"typeInfo": {"PVP": {"battle": 0}}}):
    try:
        E.battle_types(bad)
        if bad is None or isinstance(bad, (dict, list)):
            E.account_summary(bad)
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"      异常: {e!r}")
    check(ok, f"安全处理 {str(bad)[:40]}")

print()
print("=" * 72)
if FAILS:
    print(f"失败 {len(FAILS)} 项：")
    for f in FAILS:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL COVERAGE TESTS PASSED")
