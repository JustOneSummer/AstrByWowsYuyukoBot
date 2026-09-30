"""测试数据构造：严格按真实 API 响应形状。

▍为什么要单独一个文件
之前几次踩坑（单船读空、maxInfo 丢失、字段路径全错）都源于**测试数据是我猜的形状**，
而真实响应不一样。所以统一在这里按真实响应构造，改结构只改一处。

真实形状（取自 tools/fixtures/account_info.json）::

    battleTypeInfo['PVP'] = {
        "type": "PVP", "battle": True, "prInfo": {value, nextValue, name, details},
        "shipInfo": {
            "battleInfo":  {battle, wins, losses, survived, winAndSurvived},
            "avgInfo":     {damage, scoutingDamage, win, kd, frags, shipsSpotted,
                            planesKilled, artAgro, tpdAgro, xp, basicXp},
            "fragsInfo":   {frags, fragsByMain, fragsByAtba, fragsByPlanes,
                            fragsByTpd, fragsByRam, fragsByDbomb},
            "maxInfo":     {maxFrags, maxDamageDealt, ..., value 包在 {shipId, value}},
            "controlCapturedAndDroppedPointsInfo": {gameContributionToCapture, ...},
            "hitRatioInfo": {ratioMain, ratioAtba, ratioTpd, ratioTbomb},
            "expansion":   {topGrade},
            "lastBattleTime": ..., "recordTime": ...,
        },
    }

    levelInfo['10']['PVP']     = 同构
    shipTypeInfo['Battleship']['PVP'] = 同构
"""

from __future__ import annotations


def val(value, extra: dict | None = None) -> dict:
    """模拟上游的 {值 + 噪声键} 包装（maxInfo 里就是 {shipId, value}）。"""
    wrapped = {"shipId": 0, "value": value}
    if extra:
        wrapped.update(extra)
    return wrapped


def mode_node(
    battles: int = 300,
    win: float = 55.5,
    damage: int = 65000,
    frags: float = 0.85,
    kd: float = 1.4,
    xp: int = 1600,
    pr: int = 1520,
    pr_name: str = "非常好",
) -> dict:
    """一个模式节点（battleTypeInfo['PVP'] 这种）。"""
    maxed = {
        "maxFrags": val(7),
        "maxFragsByMain": val(6),
        "maxFragsByTpd": val(2),
        "maxDamageDealt": val(210000),
        "maxScoutingDamage": val(120000),
        "maxPlanesKilled": val(30),
        "maxShipsSpotted": val(12),
        "maxTotalAgro": val(3500000),
        "maxXp": val(3200),
        "maxBasicXp": val(0),
    }
    return {
        "type": "PVP",
        "battle": True,
        "prInfo": {
            "code": 6,
            "value": pr,
            "nextValue": 227,
            "name": pr_name,
            "color": "#00BCD4",
            "details": {
                "pr": pr,
                "originalServer": {"shipId": 0, "damage": 669724685.0, "frags": 6714.86, "wins": 4660.83},
                "user": {"shipId": 0, "damage": 905628767.0, "frags": 10411.0, "wins": 5813.0},
                "userServer": {"shipId": 0, "damage": 669724685.0, "frags": 6714.86, "wins": 4660.83},
            },
        },
        "shipInfo": {
            "battleInfo": {
                "battle": battles,
                "wins": int(battles * win / 100),
                "losses": battles - int(battles * win / 100),
                "survived": int(battles * 0.4),
                "winAndSurvived": int(battles * 0.3),
            },
            "avgInfo": {
                "damage": damage,
                "damageData": {"code": 3, "value": damage, "color": "#f00"},  # 渲染字段，应被过滤
                "scoutingDamage": 35282,
                "win": win,
                "winsData": {"code": 5, "value": win, "color": "#0f0"},       # 渲染字段，应被过滤
                "kd": kd,
                "frags": frags,
                "shipsSpotted": 1,
                "planesKilled": 6,
                "artAgro": 954802,
                "tpdAgro": 61043,
                "xp": xp,
                "basicXp": 0,
            },
            "fragsInfo": {
                "frags": 7687,
                "fragsByMain": 7687,
                "fragsByAtba": 142,
                "fragsByPlanes": 1505,
                "fragsByTpd": 554,
                "fragsByRam": 62,
                "fragsByDbomb": 0,
            },
            "maxInfo": maxed,
            "controlCapturedAndDroppedPointsInfo": {
                "gameContributionToCapture": 8.16,
                "gameContributionToDefense": 13.7,
            },
            "hitRatioInfo": {
                "ratioMain": 34.48,
                "ratioAtba": 18.19,
                "ratioTpd": 5.93,
                "ratioTbomb": 0.0,
            },
            "expansion": {"topGrade": 5},
            "lastBattleTime": 1643029607,
            "recordTime": 1790807949374,
        },
    }


def account_fixture() -> dict:
    """总表：userInfo + prInfo + battleTypeInfo + shipTypeInfo + levelInfo。"""
    return {
        "userInfo": {
            "server": "asia",
            "serverCn": "亚服",
            "clanInfo": {"clanId": 2000022706, "tag": "YU_RI", "name": "测试军团",
                         "description": "x", "color": "#b3b3b3"},
            "accountId": 2022515210,
            "userName": "Nahida_official",
            "accountCreateTime": 1549278775,
            "prStatus": 0,
            "dogTag": "https://x/root/2022515210.png",
            "templateId": 1,
        },
        "prInfo": {"code": 6, "value": 1868, "nextValue": 232, "name": "非常好",
                   "color": "#00BCD4", "details": {}},
        "battleTypeInfo": {
            "PVP": mode_node(9881, 63.52, 96828, 1.13, 3.12, 2295, 1873),
            "PVP_SOLO": mode_node(3118, 57.09, 93474, 1.12, 2.9, 2168, 1872, "非常好"),
            "PVP_DIV2": mode_node(2377, 60.03, 96822, 1.14, 2.88, 2282, 1839),
            "PVP_DIV3": mode_node(4386, 69.97, 99216, 1.14, 3.46, 2392, 1895),
            "RANK_SOLO": mode_node(425, 57.18, 87608, 0.97, 1.99, 2194, 1666, "很好"),
        },
        "shipTypeInfo": {
            "Battleship": {"PVP": mode_node(3915, 65.26, 117071, 1.2, 3.5, 2400, 1912)},
            "Cruiser": {"PVP": mode_node(3264, 61.89, 86827, 1.0, 2.8, 2200, 1949)},
            "Destroyer": {"PVP": mode_node(1343, 66.27, 57731, 0.9, 2.2, 2000, 1959)},
        },
        "levelInfo": {
            "10": {"PVP": mode_node(5248, 63.89, 111481, 1.1, 3.0, 2300, 1821)},
            "9": {"PVP": mode_node(1989, 64.5, 87822, 1.0, 2.7, 2100, 1988)},
        },
        "lastBattleTime": 1790344708,
        "recordTime": 1790807949374,
    }


def ship_fixture() -> dict:
    """单船：shipInfo（船只信息）+ typeInfo（各模式统计）。"""
    return {
        "userInfo": {"userName": "Nahida_official", "accountId": 2022515210,
                     "server": "asia", "prStatus": 0},
        "shipInfo": {
            "nameCn": "大和", "nameEnglish": "Yamato", "shipType": "Battleship",
            "levelStr": "X", "country": "Japan", "countryImage": "file:///jp.png",
            "shipTypeImage": "file:///bb.png", "imgSmall": "file:///yamato.png",
            "shipId": 4290689008,
        },
        "typeInfo": {
            "PVP": mode_node(120, 58.3, 88000, 1.05, 2.9, 2200, 1550),
            "RANK_SOLO": mode_node(10, 60.0, 92000, 1.0, 2.5, 2100, 1700),
        },
    }


def recent_fixture() -> dict:
    """近期：battleTypeInfo + shipInfoBattleList（逐船）。"""
    return {
        "userInfo": {"userName": "Nahida_official", "accountId": 2022515210,
                     "server": "asia", "prStatus": 0},
        "battleTypeInfo": {
            "PVP": mode_node(300, 55.5, 65000, 0.85, 1.4, 1600, 1520),
            "PVP_SOLO": mode_node(120, 52.1, 58000, 0.72, 1.1, 1400, 1350),
        },
        "shipInfoBattleList": [
            {"shipInfo": {"nameCn": "岛风", "nameEnglish": "Shimakaze", "shipType": "Destroyer",
                          "levelStr": "X", "country": "Japan", "imgSmall": "file:///d.png"},
             "typeInfo": {"PVP": mode_node(80, 54.0, 45000, 0.9, 1.3, 1500, 1400)}},
            {"shipInfo": {"nameCn": "大和", "nameEnglish": "Yamato", "shipType": "Battleship",
                          "levelStr": "X", "country": "Japan", "imgSmall": "file:///y.png"},
             "typeInfo": {"PVP": mode_node(40, 60.0, 90000, 1.1, 2.0, 2000, 1900)}},
        ],
        "lastBattleTime": 1790344708,
    }


def recents_fixture() -> dict:
    """单场近期：shipInfos（逐场）+ shipnfosTotal（按船，注意上游少个 i）。"""
    return {
        "userInfo": {"userName": "Nahida_official", "accountId": 2022515210, "server": "asia"},
        "battleTypeInfo": {"PVP": mode_node(300, 55.5, 65000, 0.85, 1.4, 1600, 1520)},
        "activate": 1790807949374,
        "shipInfos": [
            {"recordTime": 1790807949374,
             "shipInfo": {"nameCn": "岛风", "shipType": "Destroyer", "levelStr": "X"},
             "typeInfo": {"PVP": mode_node(1, 100.0, 88000, 2.0, 4.0, 3000, 2000)}},
            {"recordTime": 1790807000000,
             "shipInfo": {"nameCn": "大和", "shipType": "Battleship", "levelStr": "X"},
             "typeInfo": {"PVP": mode_node(1, 0.0, 20000, 0.0, 0.0, 800, 600)}},
        ],
        "shipnfosTotal": [
            {"shipInfo": {"nameCn": "大和", "shipType": "Battleship", "levelStr": "X"},
             "typeInfo": {"PVP": mode_node(40, 60.0, 90000, 1.1, 2.0, 2000, 1900)}},
        ],
    }


def ships_list_fixture() -> dict:
    """筛选：filter + list（每项 shipInfo + typeInfo）。"""
    return {
        "userInfo": {"userName": "Nahida_official", "accountId": 2022515210},
        "battleTypeInfo": {"PVP": mode_node(300, 55.5, 65000, 0.85, 1.4, 1600, 1520)},
        "filter": {"desc": "10 级战列舰", "levelMin": 10, "levelMax": 10},
        "list": [
            {"shipInfo": {"nameCn": "蒙大拿", "shipType": "Battleship", "levelStr": "X",
                          "country": "USA"},
             "typeInfo": {"PVP": mode_node(30, 58.0, 85000, 1.0, 2.4, 2100, 1800)}},
            {"shipInfo": {"nameCn": "大和", "shipType": "Battleship", "levelStr": "X",
                          "country": "Japan"},
             "typeInfo": {"PVP": mode_node(50, 62.0, 95000, 1.2, 3.0, 2300, 1950)}},
        ],
    }


def roll_fixture() -> dict:
    return {
        "userInfo": {"userName": "x", "accountId": 1},
        "shipInfo": {"nameCn": "岛风", "nameEnglish": "Shimakaze", "shipType": "Destroyer",
                     "levelStr": "X", "country": "Japan", "imgSmall": "file:///d.png"},
    }
