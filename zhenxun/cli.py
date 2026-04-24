"""zx CLI — 绪山真寻 Bot 命令行工具

用法:
    zx run          启动 launcher
    zx run-worker   启动 worker（由 launcher 调用）
    zx version      显示版本信息
"""

from __future__ import annotations

import atexit
import importlib.metadata
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _print_version() -> None:
    try:
        ver = importlib.metadata.version("zhenxun-bot")
    except importlib.metadata.PackageNotFoundError:
        ver = "unknown"
    sys.stdout.write(f"zhenxun-bot {ver}\n")


def _ensure_project_root() -> Path:
    cwd = Path.cwd()
    if not (cwd / "zhenxun").is_dir():
        sys.stderr.write("错误: 当前目录不是 zhenxun_bot 项目目录。\n")
        sys.stderr.write("请在项目根目录（包含 zhenxun/ 目录的位置）执行 zx run。\n")
        sys.exit(1)

    cwd_str = str(cwd)
    if cwd_str not in sys.path:
        sys.path.insert(0, cwd_str)
    return cwd


def _run_worker() -> None:
    """启动 Bot worker（必须在项目目录下执行）"""
    _ensure_project_root()

    import contextlib
    import platform

    import nonebot

    htmlrender_browser_channel = None
    system = platform.system()

    if system == "Windows":
        import winreg

        paths = {
            "chrome": r"SOFTWARE\Clients\StartMenuInternet\Google Chrome\DefaultIcon",
            "msedge": r"SOFTWARE\Clients\StartMenuInternet\Microsoft Edge\DefaultIcon",
        }
        for name, path in paths.items():
            with contextlib.suppress(FileNotFoundError):
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
                htmlrender_browser_channel = name
                break

    elif system == "Darwin":
        mac_paths = {
            "chrome": "/Applications/Google Chrome.app",
            "msedge": "/Applications/Microsoft Edge.app",
        }
        for name, path in mac_paths.items():
            if Path(path).exists():
                htmlrender_browser_channel = name
                break

    if htmlrender_browser_channel:
        nonebot.logger.info(
            f"使用 {htmlrender_browser_channel} 作为 htmlrender 驱动启动..."
        )

    from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

    nonebot.init(htmlrender_browser_channel=htmlrender_browser_channel)

    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)

    nonebot.load_plugins("zhenxun/builtin_plugins")
    nonebot.load_plugins("zhenxun/plugins")

    from zhenxun.configs.config import BotConfig

    for ext in BotConfig.ext_path:
        ext = ext.strip()
        if ext:
            nonebot.logger.info(f"加载第三方插件目录: {ext}")
            nonebot.load_plugins(ext)

    nonebot.run()


def _build_worker_command() -> list[str]:
    return [sys.executable, "-m", "zhenxun.cli", "run-worker"]


def _get_worker_creationflags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return 0


def _wait_worker_exit(proc: subprocess.Popen, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.1)
    return proc.poll() is not None


def _terminate_worker(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    soft_exit_timeout = 12.0
    terminate_timeout = 5.0
    if os.name == "nt":
        ctrl_break_event = getattr(signal, "CTRL_BREAK_EVENT", None)
        if ctrl_break_event is not None:
            try:
                proc.send_signal(ctrl_break_event)
            except Exception:
                pass
            else:
                if _wait_worker_exit(proc, soft_exit_timeout):
                    return
    if _wait_worker_exit(proc, 1.0):
        return
    try:
        proc.terminate()
    except Exception:
        pass
    else:
        if _wait_worker_exit(proc, terminate_timeout):
            return
    proc.kill()
    proc.wait(timeout=5)


def _run_launcher() -> None:
    cwd = _ensure_project_root()
    from zhenxun.utils.restart_state import (
        clear_launcher_restart_signal,
        consume_launcher_restart_signal,
    )

    clear_launcher_restart_signal()
    current_worker: subprocess.Popen | None = None
    stopping = False

    def _cleanup_current_worker() -> None:
        if current_worker is not None:
            _terminate_worker(current_worker)

    atexit.register(_cleanup_current_worker)

    def _handle_launcher_signal(signum, _frame) -> None:
        nonlocal current_worker, stopping
        if stopping:
            raise SystemExit(128 + int(signum))
        stopping = True
        clear_launcher_restart_signal()
        if current_worker is not None:
            _terminate_worker(current_worker)
        raise SystemExit(128 + int(signum))

    handled_signals = [signal.SIGINT]
    if hasattr(signal, "SIGTERM"):
        handled_signals.append(signal.SIGTERM)
    if hasattr(signal, "SIGBREAK"):
        handled_signals.append(signal.SIGBREAK)
    for sig in handled_signals:
        try:
            signal.signal(sig, _handle_launcher_signal)
        except Exception:
            pass

    while True:
        worker_env = os.environ.copy()
        worker_env["ZHENXUN_LAUNCHER_PID"] = str(os.getpid())
        worker = subprocess.Popen(
            _build_worker_command(),
            cwd=str(cwd),
            creationflags=_get_worker_creationflags(),
            env=worker_env,
        )
        current_worker = worker
        try:
            return_code = worker.wait()
        except KeyboardInterrupt:
            clear_launcher_restart_signal()
            _terminate_worker(worker)
            return
        finally:
            if current_worker is worker:
                current_worker = None

        should_restart = consume_launcher_restart_signal()
        if should_restart:
            continue
        raise SystemExit(return_code)


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] == "run":
        _run_launcher()
    elif args[0] == "run-worker":
        _run_worker()
    elif args[0] == "version":
        _print_version()
    elif args[0] in ("-h", "--help", "help"):
        sys.stdout.write((__doc__ or "") + "\n")
    else:
        sys.stderr.write(f"未知命令: {args[0]}\n")
        sys.stderr.write((__doc__ or "") + "\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
