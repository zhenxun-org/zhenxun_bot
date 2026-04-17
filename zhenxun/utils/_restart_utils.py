import asyncio
import os
import signal
import subprocess
import sys
import _thread
import atexit
from pathlib import Path

from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

_restart_pending: bool = False


def _exec_new_process() -> None:
    import platform
    import shutil

    is_windows = platform.system().lower() == "windows"
    uv_path = shutil.which("uv")
    
    # 确保路径兼容性
    cwd_path = str(Path().resolve())
    
    # Windows 下设为 0，不再弹新 CMD 窗口，实现“原地重启”
    win_creation_flag = 0

    if uv_path and not is_windows:
        try:
            os.execl(uv_path, "uv", "run", "zx")
        except Exception:
            pass

    if uv_path and is_windows:
        try:
            subprocess.Popen(
                [uv_path, "run", "zx"],
                cwd=cwd_path,
                creationflags=win_creation_flag,
            )
            os._exit(0)
        except Exception:
            pass

    try:
        subprocess.Popen(
            [sys.executable, "-m", "zhenxun"],
            cwd=cwd_path,
            creationflags=win_creation_flag,
        )
        os._exit(0)
    except Exception:
        logger.error("重启失败：无法启动新进程", "重启")
        os._exit(1)


@PriorityLifecycle.on_shutdown(priority=99)
async def _execute_restart() -> None:
    if not _restart_pending:
        return
    logger.info("所有资源已释放，正在重启进程...", "重启")
    _exec_new_process()


# 【新增】终极保底机制：即使关机过程中任何插件抛出严重异常导致进程崩溃，这里也能确保新进程被拉起
@atexit.register
def _emergency_restart() -> None:
    if _restart_pending:
        logger.warning("检测到非正常退出 (可能因插件清理报错)，触发 atexit 保底重启...", "重启")
        _exec_new_process()


async def schedule_restart() -> None:
    """触发优雅重启：引发 KeyboardInterrupt 让 NoneBot 执行完所有 shutdown hook，
    最后由 priority=99 的 hook 或 atexit 执行进程替换。
    """
    global _restart_pending
    _restart_pending = True

    async def _send_sigint() -> None:
        await asyncio.sleep(0.3)
        logger.info("发送重启信号...", "重启")
        _thread.interrupt_main()

    asyncio.create_task(_send_sigint())  # noqa: RUF006
