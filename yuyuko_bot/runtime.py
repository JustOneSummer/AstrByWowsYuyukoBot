"""hikari-core 的启动、就绪判断与收尾。

`set_hikari_config` 不是协程，内部还要同步拉模板清单 + 战舰资源（HikariBot-Official
的 `start.py` 同样是模块级同步调用），所以放线程里跑，别堵 AstrBot 的事件循环。
"""

import asyncio
import shutil

from astrbot.api import logger

from hikari_core import get_cache_file, set_hikari_config

from .config import build_hikari_config

# 启动配置的上限：超时就不再干等，转由 `is_ready()` 按「未就绪」提示用户
STARTUP_TIMEOUT = 300.0

# hikari-core 的缓存目录下，本插件放渲染产物与临时文件的子目录
TEMP_DIR_NAME = "file_img_temp"


class CoreRuntime:
    """持有 hikari-core 的启动任务，并负责它的就绪状态与资源回收。"""

    def __init__(self, config):
        self._config = config
        self._startup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """下发配置并启动 hikari-core（同步重活都在线程里，超时不阻塞事件循环）。"""
        logger.info("开始初始化wows-yuyuko插件")
        try:
            self._startup_task = asyncio.create_task(asyncio.to_thread(self._setup))
            await asyncio.wait_for(
                asyncio.shield(self._startup_task), timeout=STARTUP_TIMEOUT
            )
        except asyncio.TimeoutError:
            # shield 保证首次下载不会因为超时而半途被取消，后台继续跑完
            logger.warning(
                f"wows-yuyuko插件初始化超过 {STARTUP_TIMEOUT:.0f}s 仍未完成"
                "（首次使用需下载模板与战舰资源），将在后台继续，就绪前会提示用户稍后再试"
            )
        except Exception as e:
            logger.exception(f"wows-yuyuko插件初始化失败: {e}")

    def _setup(self) -> None:
        """在线程里执行 hikari-core 的同步启动配置。"""
        set_hikari_config(**build_hikari_config(self._config))
        # set_hikari_config 内部已 initial_cache_file()，此时缓存目录才是配置后的那个
        get_temp_dir().mkdir(parents=True, exist_ok=True)
        _prepare_browser()

    def is_ready(self) -> bool:
        """hikari-core 是否已经就绪（配置下发完成且没抛异常）。"""
        task = self._startup_task
        if task is None or not task.done():
            return False
        return task.exception() is None

    @staticmethod
    async def shutdown() -> None:
        """停调度器、关浏览器，只清理截图产物。

        删 file_img_temp/ 与 browser_temp/ 的临时 html；保留 browsers/ 与 ship_cache/。
        """
        _shutdown_scheduler()
        await _shutdown_browser()
        await clean_temp_dir()


def get_cache_dir():
    """hikari-core 当前的缓存目录（必须在 `set_hikari_config` 之后读取才有意义）。"""
    return get_cache_file()


def get_temp_dir():
    """渲染产物与临时文件的落盘目录。"""
    return get_cache_dir() / TEMP_DIR_NAME


async def clean_temp_dir() -> None:
    """删除本插件的截图落盘目录（不存在则跳过）；不动 browsers / ship_cache。"""
    temp_dir = get_temp_dir()
    if temp_dir.is_dir():
        await asyncio.to_thread(shutil.rmtree, temp_dir)


def _shutdown_scheduler() -> None:
    """停掉 hikari-core 的 APScheduler 定时任务。"""
    from hikari_core.core.config import _scheduler

    # local_test=True 时不会启动定时任务，此时 shutdown 会抛 SchedulerNotRunningError
    if _scheduler.running:
        _scheduler.shutdown()
        logger.info("hikari-core 定时任务已停止")


async def _shutdown_browser() -> None:
    """关掉 hikari-core 已启动的截图服务与浏览器进程（没启动过就跳过）。"""
    try:
        from hikari_core.Html_Render.minimal_screens_hot_service import (
            minimal_screens_hot_service,
        )

        service = minimal_screens_hot_service._instance
        if service is None:
            return
        await service.close()
        logger.info("hikari-core 截图服务已关闭")
    except Exception as e:
        logger.warning(f"关闭截图服务失败: {e}")


def _prepare_browser() -> None:
    """初始化时下载好 Playwright 内核（同步阻塞，放在线程里跑，别堵事件循环）。"""
    from hikari_core.Html_Render.minimal_screens_hot_service import (
        minimal_screens_hot_service,
    )
    from hikari_core.core.config import hikari_config

    browser = hikari_config.use_broswer or "chromium"
    executable = minimal_screens_hot_service.setup_playwright(browser=browser)
    if not executable:
        raise RuntimeError(
            f"Playwright 浏览器内核（{browser}）准备失败，请检查网络/代理后重载插件"
        )
    logger.info(f"Playwright 浏览器内核已就绪: {executable}")
