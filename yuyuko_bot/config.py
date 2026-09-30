"""AstrBot 插件配置 -> hikari-core 配置的映射。

`_conf_schema.json` 里的每一项都在这里读取并转成一个关键字参数，
`main.py` 只管调用，不再逐个 `self.config.get(...)`。

注意：包内已有 `_conf_schema.json` 这个**配置清单**文件，本模块名 `config.py`
指的是「读取配置的代码」，两者不是一回事；插件数据目录是
`data/plugin_data/<插件名>`（见 `StarTools.get_data_dir`），与插件源码目录分开，
所以这里叫 `config` 不会和数据文件撞名。
"""

from astrbot.api import AstrBotConfig
from astrbot.core.star import StarTools

# 插件数据目录名（AstrBot 会创建 data/plugin_data/<这个名字>）
DATA_DIR_NAME = "wows-yuyuko"


def build_hikari_config(config: AstrBotConfig) -> dict:
    """把面板配置转成 `set_hikari_config(**kwargs)` 的入参。"""
    proxy = config.get("wows_proxy") if config.get("wows_proxy_status") else None
    return {
        # 关掉开关时同时把地址置空，避免残留一个连不上的代理
        "proxy": proxy or None,
        "http2": config.get("http2_status"),
        "token": config.get("wows_token"),
        "use_broswer": config.get("use_broswer"),
        "image_type": config.get("image_type"),
        "local_test": config.get("local_test"),
        "save_template_html": config.get("save_template_html"),
        "render_error_fallback": config.get("render_error_fallback"),
        "user_image_cache_ttl_minutes": config.get("user_image_cache_ttl_minutes"),
        # 缓存目录固定在本插件的数据目录下（AstrBot 会随插件一起管理）
        "game_path": str(StarTools.get_data_dir(DATA_DIR_NAME)),
    }
