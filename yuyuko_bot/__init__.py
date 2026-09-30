"""战舰世界-Yuyuko 插件的实现包。

`main.py` 只保留 AstrBot 接入层（指令注册 + Star 子类），具体实现拆在这里：

- `config.py`         AstrBot 配置 -> hikari-core 配置的映射
- `runtime.py`        hikari-core 的启动 / 就绪判断 / 收尾
- `selection.py`      「等用户回复序号」的选择流程
- `sender.py`         渲染结果的落盘与发送
- `logging_bridge.py` 把 hikari-core 的 loguru 日志接进 AstrBot 的日志系统

本文件负责把随仓库内置的 hikari-core 源码挂进 `sys.path`，因此**必须先于**
上面四个子模块被导入；`main.py` 也是先 `import yuyuko_bot` 再取具体符号的。
"""

import sys
from pathlib import Path

# hikari_core 以「随仓库提交」的源码内置在插件目录下（AstrBot 的源码包不含
# submodule，装完会是空的），把插件根目录挂进 sys.path 就能直接 import。
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_HIKARI_CORE = _PLUGIN_ROOT / "hikari_core"
if (_HIKARI_CORE / "__init__.py").is_file():
    if str(_PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_ROOT))
else:
    # 不在这里 raise：让报错停在真正用到的那个 import 上，同时先给一条能看懂的提示
    import warnings

    warnings.warn(
        f"内置的 hikari-core 源码缺失或不完整（{_HIKARI_CORE}），"
        "插件将无法工作。请重新安装/更新本插件（不要只拷贝单个 main.py）。",
        RuntimeWarning,
        stacklevel=2,
    )

# 装日志桥接，必须在导入 hikari_core 之前（详见 logging_bridge）
from .logging_bridge import install as install_logging_bridge  # noqa: E402

install_logging_bridge()

from .runtime import CoreRuntime  # noqa: E402,F401
from .selection import SelectionManager  # noqa: E402,F401
from .sender import OutputSender, reply_text  # noqa: E402,F401
