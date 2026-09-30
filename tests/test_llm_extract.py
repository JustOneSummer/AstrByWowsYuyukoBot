#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 提取/输出端到端测试工具。

真实调用 yuyuko API，然后把三段内容完整打印出来，方便肉眼核对：

    ① 原始 Output.Data（上游返回）
    ② 提取器产出的精简字段（就是喂给 LLM 的数据）
    ③ 给 LLM 的最终文本（含点评引导 / 出错话术）

▍用法

    # 以「查自己」的方式测试（accountId 当平台 ID 用）
    python tools/test_llm_extract.py

    # 指定账号 / 平台 ID / 各种查询
    python tools/test_llm_extract.py --account-id 2022515210
    python tools/test_llm_extract.py --case ship --ship 大和
    python tools/test_llm_extract.py --case all          # 跑一遍所有查询
    python tools/test_llm_extract.py --save resp.json    # 顺手存下原始响应

    # token 从环境变量或参数给（默认值仅供本地调试，别提交自己的 token）
    set WOWS_TOKEN=xxxxx:yyyyy
    python tools/test_llm_extract.py --token xxxxx:yyyyy

▍它会做什么

- 用 `set_hikari_config(local_test=True)` 初始化：**跳过 OSS 模板更新与定时任务**，
  避免每次测试都下载模板清单（这是 hikari 自己的测试开关）
- 用 `init_hikari_no_output` 取数据（不渲染图片），再单独走一遍提取逻辑
- 需要浏览器渲染的步骤会被跳过，所以不需要装 chromium

▍注意

会向 yuyuko API 发真实请求（消耗 token 配额），并把战舰资源缓存到
`tests/_test_cache/`（首次约 18MB，之后复用）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 让脚本能直接跑：把插件根目录挂进 sys.path，`from hikari_core import ...` 才可用
# ---------------------------------------------------------------------------
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# 测试用的缓存目录（与插件运行时的 data/ 分开，不污染线上数据）
TEST_CACHE = PLUGIN_ROOT / "tests" / "_test_cache"

# yuyuko 的公开测试 token（本来就是公开的），也可以用 --token / 环境变量覆盖
DEFAULT_TOKEN = os.environ.get("WOWS_TOKEN", "2622749113:TAN9iMARSDJbzLVOUK1a9cTSiKtb32GIbpr")


DEFAULT_PLATFORM_ID = "2622749113"
# 平台类型：QQ（对应 AstrBot 的 aiocqhttp 适配器）
DEFAULT_PLATFORM = "QQ"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def _dump(label: str, value, limit: int = 3000) -> None:
    """打印一段 JSON（超长截断），中文不转义。"""
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = str(value)
    if len(text) > limit:
        text = text[:limit] + f"\n…（共 {len(text)} 字符，已截断）"
    print(f"\n----- {label} -----")
    print(text)


# ---------------------------------------------------------------------------
# 各测试用例：命令**复用插件自己的构建函数**，这样测的就是真实链路
# （而不是脚本里另写一份字符串，两边容易漂移）
# ---------------------------------------------------------------------------
def _case_table():
    """构建用例表。

    ▍两种测试身份的方式
    - **不传 --nickname**：走「查自己」，用 --platform-id 当平台用户 ID，
      需要该用户已经绑定了游戏账号（插件线上就是这个路径）。
    - **传 --nickname [--server]**：走「服务器+昵称」查询，
      **不需要任何绑定**，所以可以拿任意账号测 —— 推荐排查提取问题时用这个。
    """
    from yuyuko_llm import (
        COMMAND_BUILDERS,
        build_account_command,
        build_literal_command,
        build_recent_command,
        build_roll_command,
        build_ship_command,
        build_ship_recent_command,
        build_ships_command,
    )

    return {
        "account": (
            lambda ship, sv, nn: build_account_command(sv, nn),
            "account",
            "总表（水表）：账号信息 + 各模式统计 + 船型/等级分布",
        ),
        "ship": (
            lambda ship, sv, nn: build_ship_command(ship, sv, nn),
            "ship",
            "单船：船只信息 + 各模式统计（typeInfo 节点）",
        ),
        "recent": (
            lambda ship, sv, nn: build_recent_command(7, sv, nn),
            "recent",
            "近期：各模式统计 + 逐船明细（battleTypeInfo 节点）",
        ),
        "recent_random": (
            lambda ship, sv, nn: build_recent_command(
                7, sv, nn, mode=COMMAND_BUILDERS["recent_random"]
            ),
            "recent",
            "近期随机战",
        ),
        "recent_rank": (
            lambda ship, sv, nn: build_recent_command(
                7, sv, nn, mode=COMMAND_BUILDERS["recent_rank"]
            ),
            "recent",
            "近期排位战",
        ),
        "ship_recent": (
            lambda ship, sv, nn: build_ship_recent_command(ship, 7, sv, nn),
            "recent",
            "单船近期",
        ),
        "recents": (
            lambda ship, sv, nn: build_literal_command("recents", sv, nn),
            "recents",
            "单场近期：逐场明细 + 按船汇总",
        ),
        "ships": (
            lambda ship, sv, nn: build_ships_command(8, 10, None, None, sv, nn),
            "ships",
            "筛选查询：命中的船列表（会返回候选，需二次选择）",
        ),
        "roll": (
            lambda ship, sv, nn: build_roll_command(),
            "roll",
            "随机战舰",
        ),
    }


ALL_ORDER = ["account", "ship", "recent", "recent_random", "recent_rank",
             "ship_recent", "recents", "ships", "roll"]


async def run_case(
    name: str,
    *,
    token: str,
    platform_id: str,
    bot_id: str,
    server: str | None,
    nickname: str | None,
    ship: str,
    save_path: Path | None,
    raw_only: bool,
    fixture: Path | None = None,
    platform: str = DEFAULT_PLATFORM,
    verbose: bool = False,
    truncate: bool = False,
) -> bool:
    """跑一个用例。返回是否成功拿到数据。

    ``fixture`` 给了就读本地 JSON（离线，不打 API），否则真实调用。
    默认只打印**最终给 LLM 的文本**；``verbose`` 才额外打印提取结果与原始数据。
    """
    builder, extractor, desc = _case_table()[name]
    command_text = builder(ship, server, nickname)

    if fixture is not None:
        from hikari_core import Hikari_Model

        raw = json.loads(fixture.read_text(encoding="utf-8"))
        hikari = Hikari_Model()
        hikari.success(raw)
        hikari.Output.Template = "(离线夹具，无模板)"
        source = f"离线夹具 {fixture}"
    else:
        from hikari_core import init_hikari_no_output

        hikari = await init_hikari_no_output(
            command_text=command_text,
            platform=platform,
            PlatformId=platform_id,
            BotId=bot_id,
            GroupId=None,
        )
        source = f"平台 {platform}/{platform_id}"

    _hr(f"用例 {name} —— {desc}")
    identity = "查自己（走绑定关系）" if not nickname else f"查 {server or ''} {nickname}".strip()
    print(f"{source}  |  command_text {command_text!r}  |  {identity}  |  {hikari.Status}")
    if verbose:
        print(f"模板: {hikari.Output.Template}")

    if save_path:
        try:
            save_path.write_text(
                json.dumps(hikari.Output.Data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"已保存原始响应: {save_path}")
        except Exception as e:
            print(f"保存失败: {e}")

    if hikari.Status == "wait":
        _hr("需要用户二次选择（ships 这类）")
        options = hikari.Input.Select_Data or []
        print(f"候选条数: {len(options)}")
        _dump("候选项（前 5 条）", options[:5], limit=1500)
        print("\n这属于正常流程：实际使用时会把这些转述给用户挑序号。")
        return True

    if hikari.Status != "success":
        _hr("查询未成功（这本身就是一种结果）")
        print(f"Status = {hikari.Status}")
        print(f"Output.Data（会给用户的错误文案）: {hikari.Output.Data}")
        return False

    if raw_only:
        _dump("原始 Output.Data", hikari.Output.Data)
        return True

    # ---------------- 提取 ----------------
    from yuyuko_llm import BattleStatsExtractor as E

    raw = hikari.Output.Data
    extractors = {
        "account": E.account_summary,
        "ship": E.ship_summary,
        "recent": E.recent_summary,
        "recents": E.recents_summary,
        "ships": E.ships_summary,
        "roll": E.roll_summary,
    }
    extracted = extractors.get(extractor, lambda d: d)(raw)

    if not extracted:
        print("\n[!] 提取结果是空的：字段路径跟上游对不上（不是上游没数据）")
        if fixture is not None:
            print(f"    （当前夹具是 {fixture.name}，可能不是「{name}」这个查询的响应；"
                  f"离线测试请用对应的夹具）")

    # 细节默认不打印 —— 平时只关心「最终给 LLM 的是什么」，用 --verbose 才展开
    if verbose:
        _dump("提取结果（喂给 LLM 的字段）", extracted)
        _dump("原始 Output.Data（对照用，看提取漏没漏）", raw)

    # ---------------- 给 LLM 的文本 ----------------
    _hr("③ 给 LLM 的最终文本" if verbose else "给 LLM 的最终文本")
    print(_format_for_llm(extractor, extracted, truncate=truncate))

    _report_missing(name, raw, extracted)
    return True


def _format_for_llm(extractor: str, extracted, truncate: bool = False) -> str:
    """复刻 ToolExecutor._format_payload 的输出（不依赖 event / runtime）。

    ``truncate=False``（默认）：不截断，测试时看全量数据。
    传 True 则按插件的 MAX_PAYLOAD_CHARS 截断，用来核对线上真实会喂多少。
    """
    sys.path.insert(0, str(PLUGIN_ROOT))
    from yuyuko_llm.executor import MAX_PAYLOAD_CHARS
    from yuyuko_llm.tool_spec import SERVER_CODE_HINT

    if isinstance(extracted, str):
        text = extracted.strip()
        if not text:
            return "操作已完成，但没有返回可读信息，请如实告知用户。"
        head = f"操作已成功，以下是系统返回的原话，请自然地向用户转述。{SERVER_CODE_HINT}\n"
        return head + text

    if not extracted:
        return "查询成功，但没有提取到可读数据（可能是该账号在该条件下没有记录），请如实告知用户。"

    try:
        text = json.dumps(extracted, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(extracted)
    if truncate and len(text) > MAX_PAYLOAD_CHARS:
        text = text[:MAX_PAYLOAD_CHARS] + f"…（已截断，原长 {len(text)} 字符）"
    elif not truncate and len(text) > MAX_PAYLOAD_CHARS:
        # 测试模式不截断，但提示一下线上会截
        print(
            f"[提示] 该数据 {len(text)} 字符，超过线上阈值 {MAX_PAYLOAD_CHARS}，"
            f"实际会截断（加 --truncate 看截断后的效果）"
        )

    head = (
        "以下是用户要查询的战绩数据（JSON），请据此作答，"
        "并用自然的语气点评一下玩家的表现（水平、风格、亮点或不足）。"
        "图片已经发给用户了，不需要再把数据罗列一遍。\n\n"
    )
    return head + text


# 各界面模板会读、用来对照是否漏提的关键字段
_EXPECT: dict[str, tuple[str, ...]] = {
    "account": ("battle_types", "ship_types", "levels", "pr"),
    "ship": ("ship_name", "ship_level", "battle_types"),
    "recent": ("battle_types",),
    "recent_random": ("battle_types",),
    "recent_rank": ("battle_types",),
    "ship_recent": ("battle_types",),
    "recents": ("recent_battles", "by_ship"),
    "ships": ("ships",),
    "roll": ("ship_name",),
}


def _report_missing(case: str, raw, extracted) -> None:
    """粗查一下该界面关心的顶层字段有没有缺。"""
    if not isinstance(extracted, dict):
        return
    expect = _EXPECT.get(case, ())
    missing = [k for k in expect if not extracted.get(k)]
    if missing:
        print(f"\n⚠️  该界面预期字段缺失: {missing}")


async def main() -> int:
    ap = argparse.ArgumentParser(
        description="LLM 提取/输出端到端测试（真实调用 yuyuko API）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--case", default="recent",
                    help=f"用例：{', '.join(ALL_ORDER)}，或 all 跑全部（默认 recent）")
    ap.add_argument("--token", default=DEFAULT_TOKEN,
                    help="yuyuko API token（默认用内置的公开 token；也可用环境变量 WOWS_TOKEN 覆盖）")
    ap.add_argument("--platform-id", default=DEFAULT_PLATFORM_ID,
                    help="平台侧用户 ID（QQ 号，如 2622749113）。不传 --nickname 时用它走「查自己」，"
                         "需该平台用户已绑定游戏账号。注意：**不是**游戏 accountId")
    ap.add_argument("--platform", default=DEFAULT_PLATFORM,
                    help="平台类型（默认 QQ；对应 AstrBot 的 aiocqhttp 适配器）")
    ap.add_argument("--nickname", default=None,
                    help="游戏昵称：给了就走「服务器+昵称」查询，不需要绑定，推荐排查提取问题时用")
    ap.add_argument("--server", default="asia",
                    help="服务器（配合 --nickname；可选 asia/cn/eu/na/ru）")
    ap.add_argument("--bot-id", default="10000", help="机器人自身 ID")
    ap.add_argument("--ship", default="大和", help="单船用例的船名（默认 大和）")
    ap.add_argument("--save", default=None, help="把原始响应存成 JSON 文件")
    ap.add_argument("--fixture", default=None,
                    help="用本地 JSON 夹具离线测试提取（不打 API）；配合 --save 先存一份即可反复跑")
    ap.add_argument("--raw-only", action="store_true",
                    help="只打印原始响应（排查上游返回什么时用）")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="额外打印提取结果与原始数据（默认只给最终喂给 LLM 的文本）")
    ap.add_argument("--truncate", action="store_true",
                    help="按插件的 MAX_PAYLOAD_CHARS 截断（默认不截断，方便看全量）")
    args = ap.parse_args()

    platform_id = args.platform_id
    platform = args.platform

    use_fixture = bool(args.fixture)
    if not use_fixture and not args.token:
        # 只在用户显式把 token 清空时才会走到这里
        print("[!] 缺少 token：用 --token 传入或设环境变量 WOWS_TOKEN")
        print("    （只想验证提取逻辑的话，可以加 --fixture tests/fixtures/account_info.json 离线跑）")
        return 2

    if args.verbose:
        _hr("初始化 hikari-core" + ("（离线夹具模式）" if use_fixture else ""))
    from hikari_core import set_hikari_config

    TEST_CACHE.mkdir(parents=True, exist_ok=True)
    # local_test=True：跳过 OSS 模板更新与定时任务（hikari 自带的测试开关）
    set_hikari_config(
        token=args.token,
        game_path=str(TEST_CACHE),
        use_broswer="chromium",
        image_type="jpeg",
        local_test=True,
    )
    if args.verbose:
        print(f"缓存目录: {TEST_CACHE}")
        if args.token:
            print(f"token   : {args.token[:12]}...")
        if use_fixture:
            print("离线模式：不会发起任何 API 请求")

    cases = ALL_ORDER if args.case == "all" else [args.case]
    bad = [c for c in cases if c not in ALL_ORDER]
    if bad:
        print(f"[!] 未知用例: {bad}\n可用: {', '.join(ALL_ORDER)}")
        return 2

    save_path = Path(args.save) if args.save else None
    fixture = Path(args.fixture) if args.fixture else None
    if fixture and not fixture.is_file():
        print(f"[!] 夹具不存在: {fixture}")
        return 2

    results: dict[str, bool] = {}
    for i, case in enumerate(cases):
        try:
            results[case] = await run_case(
                case,
                token=args.token,
                platform_id=args.platform_id,
                bot_id=args.bot_id,
                server=args.server or None,
                nickname=args.nickname,
                ship=args.ship,
                save_path=save_path if i == 0 else None,
                raw_only=args.raw_only,
                fixture=fixture,
                platform=platform,
                verbose=args.verbose,
                truncate=args.truncate,
            )
        except Exception:
            import traceback
            _hr(f"用例 {case} 抛异常")
            traceback.print_exc()
            results[case] = False

    _hr("汇总")
    for case, ok in results.items():
        # 只用 ASCII 标记：Windows 控制台默认 GBK，特殊符号会直接抛 UnicodeEncodeError
        print(f"  [{'OK' if ok else '!!'}] {case}")
    failed = [c for c, ok in results.items() if not ok]
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n已中断")
        raise SystemExit(130)
