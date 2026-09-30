# 战舰世界yuyuko战绩查询机器人

## 关于 hikari-core

本插件**不再从 PyPI 安装 hikari-core**，而是把 Hikari-core-v2 的源码随仓库一起分发，
放在 `hikari_core/hikari_core/` 目录下（当前内置 v2.0.1），包的 `__init__.py` 在启动时把
外层 `hikari_core/` 挂进 `sys.path`，因此插件的依赖清单里已经没有 `hikari-core`。

从 Hikari-core-v2 仓库同步源码（以源仓库的 git 跟踪文件集为准）：

```
python tools/sync_hikari_core.py                 # 默认源：D:/GitHub/Hikari-core-v2
python tools/sync_hikari_core.py --src <路径>     # 指定源仓库
python tools/sync_hikari_core.py --check         # 只对比差异，不写入
```

升级 hikari-core 的方法是**更新本插件**（重新拉取仓库 / 在 AstrBot WebUI 里点更新），
而不是在面板里 pip 安装 hikari-core。

## 代码结构

插件根目录只保留入口与 AstrBot 需要的清单文件，实现拆在两个包里：

```
main.py                  AstrBot 接入层 / 唯一入口
                         （@filter 指令 + @llm_tool 工具 + Star 子类）
yuyuko_bot/              插件骨架
├── __init__.py          内置 hikari-core 的 sys.path 注入
├── config.py            AstrBot 面板配置 -> hikari-core 配置的映射
├── runtime.py           hikari-core 的启动 / 就绪判断 / 收尾
├── selection.py         「等用户回复序号」的选择流程
├── sender.py            渲染结果的落盘与发送
└── logging_bridge.py    把 hikari-core 的 loguru 日志接进 AstrBot
yuyuko_llm/              面向 LLM 的工具层
├── extract.py           返回数据 -> 给模型看的精简字段（加字段只改这里）
├── query.py             查询编排 + 参数拼成 hikari 指令
├── tool_spec.py         工具描述文案（模型据此选工具）
├── guide.py             开关 / 限流 / 错误话术
└── executor.py          一次工具调用的公共流程
```

## 指令

发送 wws help 查看
## AI 工具（function-calling）

开启后用户可以直接用自然语言查询（「我最近打得怎么样」「查一下 XX 的水表」），
不必记 `/wws` 指令。图片照旧渲染给用户，AI 拿到的是**提取后的结构化字段**，
用于顺带点评玩家表现。

| 工具 | 用途 |
|---|---|
| `wws_account` | 战绩总表（水表） |
| `wws_ship` | 指定单船战绩 |
| `wws_recent` | 近期战绩汇总（默认全部模式） |
| `wws_recent_random` | 近期随机战 |
| `wws_recent_rank` | 近期排位战 |
| `wws_ship_recent` | 单船近期战绩 |
| `wws_recent_battles` | 逐场战斗明细 |
| `wws_ships` | 按等级/国家/舰种筛选 |
| `wws_roll_ship` | 随机抽船（娱乐） |
| `wws_bind` | 绑定游戏账号（AI 会主动引导） |
| `wws_bind_list` | 查看已绑定账号 |
| `wws_bind_change` | 切换绑定账号 |

配置项（面板 → 插件配置）：
`llm_tool_enable` 总开关、`llm_tool_evaluate` 是否点评、`llm_tool_rate_limit` 限流，
以及 12 个 `llm_tool_enable_<工具名>` 独立开关。

### 出错时怎么回复用户

工具层把故障分成两类，因为对用户的意义完全不同（见 `yuyuko_llm/guide.py` 的
`classify_error`）：

| 类型 | 判定 | 给用户的说法 |
|---|---|---|
| **网络波动** | 超时 / 连接失败 / 上游 5xx / SSL / 代理 | 提示稍后**重试** |
| **程序异常** | 其他所有异常（我们自己的 bug） | 明确告知出错，**请加群反馈**给作者 |

之所以不笼统回一句「请稍后重试」：程序异常用户重试多少次都没用，只会一直白试，
问题也永远暴露不出来。所以这类会引导用户加群 `967546463` 反馈。

反馈入口集中在 `yuyuko_llm/tool_spec.py` 的 `FEEDBACK_CONTACT`，要换渠道（改成
GitHub issue 等）只改这一处。

另外两个细节：

- **原始报错不会交给模型**。AstrBot 会把工具异常包成
  `Exception("Tool execution error: … Traceback: …")` 回给模型
  （`astrbot/core/astr_agent_tool_exec.py`），模型会照着念堆栈。所以 `main.py` 的
  `tool_guard` 装饰器在工具最外层接住异常，只回行动指引。
- **发图失败不再静默**。图片发不出去时，模型会收到数据并改用文字汇报结论，
  而不是若无其事地继续点评（那样用户什么都收不到，看着像卡死）。
  注意区分「没有图片产物」（正常，如关闭了 `auto_image`）与「发图失败」。

### 战斗模式口径

`battleTypeInfo` 下的 5 个模式是**同构**的，提取时共用一套字段：

| 数据键 | 模板标签 | 含义 |
|---|---|---|
| `PVP` | 随机 | 全部随机战（不限组队人数） |
| `PVP_SOLO` | 单野 | 独自随机，1 人 |
| `PVP_DIV2` | 自行车 | 2 人组队 |
| `PVP_DIV3` | 三轮车 | 3 人组队 |
| `RANK_SOLO` | 排位 | 排位战 |

`wws_recent` 默认返回**全部有场次的模式**，由 AI 自行判断该点评哪个；
指定 `battle_mode` 时只取该模式。参数填中文即可，别名（单人/双排/三人车…）
由 `yuyuko_llm/extract.py` 的别名表容错。

### 排查：看提取结果

每次工具调用都会把**提取后的数据**打进日志（`yuyuko_llm/executor.py` 的
`_log_extracted`），格式如下：

```
[战绩提取] 类型=ship 结果=dict, 2 个键: ['ship_name', 'battle_types']
{"ship_name":"大和","battle_types":{"PVP":{"battles":120,"win_rate":58.3, ...}}}
```

- `结果=EMPTY` 表示**一个字段都没提取到** —— 这几乎总是字段路径跟上游对不上，
  而不是上游没数据。这两种情况从用户侧看都是「AI 说没有数据」，靠这条日志区分。
- 超过 4000 字符会截断（记录中会标出原始长度）。
- 日志本身全程包了 try/except，不会因为序列化失败影响业务。

### 提取字段与模板一一对应

提取器按**各界面模板实际读取的字段**来提取（`wws-info-v6.html` / `wws-ship-v6.html` /
`wws-info-recent*.html` / `wws-ships-v6.html` / `wws-info-recents-v6.html`），做到 1:1：

| 界面 | 节点 | 提取内容 |
|---|---|---|
| 总表 | `battleTypeInfo` | 各模式统计 + `prInfo` + `lastBattleTime` + `userInfo.prStatus` |
| 总表 | `shipTypeInfo` / `levelInfo` | 船型/等级分布（场次、胜率、场均、PR） |
| 单船 | **`typeInfo`** | 各模式统计（含最高纪录、击杀构成、PR 明细） |
| 近期 | `battleTypeInfo` + `shipInfoBattleList` | 各模式统计 + 逐船明细 |
| 单场 | `shipInfos` + `shipnfosTotal` | 逐场记录 + 按船汇总 |
| 筛选 | `battleTypeInfo` + `filter` + `list` | 筛选条件 + 命中的船 |

单个模式提取出来的字段（对照模板逐项）：

```
battles survived win_rate avg_damage avg_frags avg_kd avg_xp planesKilled
fragsByMain fragsByTpd fragsByPlanes fragsByRam fragsByDbomb fragsByAtba
lastBattleTime
maxDamageDealt maxFrags maxPlanesKilled maxScoutingDamage maxTotalAgro maxXp
hit_ratio pr   (+ prInfo.details.originalServer 的 damage/frags/wins)
```

几个容易踩的点，都已处理：

- **模式节点名有两种**：近期/总表是 `battleTypeInfo`，单船与筛选列表是 `typeInfo`。
  只认一个会让另一边读成空（表现为「AI 说没有数据」）。
- **最高纪录在 `shipInfo.maxInfo` 下**，不在 `battleInfo` 里 —— 所以用递归提取，
  而不是把路径写死。
- **上游大量用 `{value, color}` 包装**：递归时按**父键名**记值
  （`maxDamageDealt: 210000`），否则只会拿到一堆互相覆盖的 `value`。
- **渲染专用字段被过滤**（`color` / `winsData` / `damageData`），省 token 也不干扰模型。
- **中文名不在数据里**：船型中文（`Battleship → 战列舰`）与服务器中文（`asia → 亚服`）
  都是模板前端写死的映射，Python 侧自己备了一份。

### 加字段 / 加工具

- **加提取字段**：优先改 `yuyuko_llm/extract.py` 的白名单常量
  （`BATTLE_FIELDS` / `BATTLE_SUBTREES` / `ACCOUNT_FIELDS` / `SHIP_FIELDS` /
  `ROW_FIELD_CANDIDATES` / `DIST_NODES`）。
  落在 `BATTLE_SUBTREES` 那几棵子树里的新字段会被**递归自动带出来**，通常不用改代码。
- **加工具**：在 `main.py` 加一个带 `@llm_tool` 的方法（**必须在这个文件**，
  AstrBot 按模块路径匹配工具归属）并套上 `@tool_guard`，在 `yuyuko_llm/guide.py` 的
  `TOOL_SWITCH_FIELDS` 登记开关，并往 `_conf_schema.json` 补对应配置项。
- **改工具描述**：改 `main.py` 里那个方法的 **docstring**。模型看到的 `description`
  与参数说明都由 docstring 生成，写进 `yuyuko_llm/tool_spec.py` 不生效
  （那里只放出错话术、绑定引导这类运行时文案）。

> ⚠️ `@llm_tool` 生成的 schema 只支持 `type` / `name` / `description`，
> **没有 enum**，无法在协议层限制取值。所以候选值要写进描述，并在运行时做别名容错。

## onebot v11 配置

```
ws://127.0.0.1:8080/onebot/v11/ws
```

或者

```
ws://127.0.0.1:8080/ws
```

## 安装字体（yuyuko模板使用的是微软雅黑）

### 创建字体目录（如果不存在）
```
sudo mkdir -p /usr/share/fonts/truetype/microsoft/
```

从Windows把雅黑相关字体复制到上面的目录

### 更新字体缓存
```
sudo fc-cache -fv
```

然后重启astrbot，不行就重启系统

### 浏览器内核

渲染依赖 playwright 的浏览器内核。首次渲染时若内核缺失，hikari-core 会**自动**执行
`playwright install chromium`（已改用 npmmirror 镜像源，无需手动处理）；
也可以通过 `use_broswer` 配置项切换为 firefox。
