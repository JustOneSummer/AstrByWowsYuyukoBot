# 战舰世界yuyuko战绩查询机器人

## 关于 hikari-core

本插件**不再从 PyPI 安装 hikari-core**，而是把 Hikari-core-v2 的源码随仓库一起分发，
放在 `hikari_core/hikari_core/` 目录下（当前内置 v2.0.1），包的 `__init__.py` 在启动时把
外层 `hikari_core/` 挂进 `sys.path`，因此插件的依赖清单里已经没有 `hikari-core`。

之所以不用 git submodule（HikariBot-Official 的做法）：AstrBot 安装/更新插件走的是
GitHub 源码包（`archive/refs/heads/<branch>.zip`），压缩包里**不含子模块内容**，
用户装完只会得到一个空的 `hikari_core/` 而直接 ImportError。

从 Hikari-core-v2 仓库同步源码（以源仓库的 git 跟踪文件集为准）：

```
python tools/sync_hikari_core.py                 # 默认源：D:/GitHub/Hikari-core-v2
python tools/sync_hikari_core.py --src <路径>     # 指定源仓库
python tools/sync_hikari_core.py --check         # 只对比差异，不写入
```

升级 hikari-core 的方法是**更新本插件**（重新拉取仓库 / 在 AstrBot WebUI 里点更新），
而不是在面板里 pip 安装 hikari-core。

## 代码结构

插件根目录只保留入口与 AstrBot 需要的清单文件，实现全在 `yuyuko_bot/` 包里：

```
main.py                  AstrBot 接入层 / 唯一入口（指令注册 + Star 子类）
yuyuko_bot/
├── __init__.py          内置 hikari-core 的 sys.path 注入
├── config.py            AstrBot 面板配置 -> hikari-core 配置的映射
├── runtime.py           hikari-core 的启动 / 就绪判断 / 收尾
├── selection.py         「等用户回复序号」的选择流程
└── sender.py            渲染结果的落盘与发送
```

> ⚠️ 带 `@filter.xxx` 装饰器的指令方法**必须留在根目录的 `main.py`**：
> AstrBot 按 `data.plugins.<插件目录>.main` 这个模块路径去匹配指令处理器
> （见 `astrbot/core/star/star_manager.py`），换到子模块里会匹配不上、指令直接消失。

导入约定：`main.py` 用相对导入 `from .yuyuko_bot import ...`，不往 `sys.path`
里塞插件自身目录 —— AstrBot 本来就把插件当包加载，相对导入稳定，也不会在
`sys.path` 上留下一个可能解析到别处的同名包。

添加配置项的流程：`_conf_schema.json` 加字段 → `yuyuko_bot/config.py` 的
`build_hikari_config()` 里映射 → `yuyuko_bot/runtime.py` 负责下发。

暂时仅支持 QQ 个人，频道版本后续支持。

## 指令

- `wws <指令>` — 查询战绩，`wws help` 查看完整指令表
- `wws 检查更新` / `wws 更新样式` / `wws 更新战舰` — 仅管理员可用

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
