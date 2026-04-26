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

GRACEFUL_SHUTDOWN_TIMEOUT = 15
WORKER_POLL_INTERVAL = 0.1
RESTART_POLL_INTERVAL = 0.5
WORKER_SOFT_EXIT_TIMEOUT = 15.0
WORKER_TERMINATE_TIMEOUT = 5.0
WORKER_KILL_TIMEOUT = 5.0


def _launcher_log(message: str) -> None:
    sys.stderr.write(f"[zx launcher] {message}\n")
    sys.stderr.flush()


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

    nonebot.run(timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT)


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
        time.sleep(WORKER_POLL_INTERVAL)
    return proc.poll() is not None


def _terminate_worker(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    _launcher_log(f"stopping worker pid={proc.pid}")
    if os.name == "nt":
        ctrl_break_event = getattr(signal, "CTRL_BREAK_EVENT", None)
        if ctrl_break_event is not None:
            try:
                _launcher_log(f"sending CTRL_BREAK_EVENT to worker pid={proc.pid}")
                proc.send_signal(ctrl_break_event)
            except Exception as e:
                _launcher_log(f"failed to send CTRL_BREAK_EVENT: {e!r}")
            else:
                if _wait_worker_exit(proc, WORKER_SOFT_EXIT_TIMEOUT):
                    _launcher_log(
                        f"worker pid={proc.pid} exited after CTRL_BREAK_EVENT "
                        f"with code {proc.returncode}"
                    )
                    return
                _launcher_log(
                    f"worker pid={proc.pid} did not exit after "
                    f"{WORKER_SOFT_EXIT_TIMEOUT:.0f}s"
                )
    if _wait_worker_exit(proc, 1.0):
        return
    try:
        _launcher_log(f"terminating worker pid={proc.pid}")
        proc.terminate()
    except Exception as e:
        _launcher_log(f"failed to terminate worker: {e!r}")
    else:
        if _wait_worker_exit(proc, WORKER_TERMINATE_TIMEOUT):
            _launcher_log(
                f"worker pid={proc.pid} exited after terminate with code "
                f"{proc.returncode}"
            )
            return
        _launcher_log(f"worker pid={proc.pid} did not exit after terminate timeout")
    _launcher_log(f"killing worker pid={proc.pid}")
    proc.kill()
    proc.wait(timeout=WORKER_KILL_TIMEOUT)


def _run_launcher() -> None:
    cwd = _ensure_project_root()
    from zhenxun.utils.restart_state import (
        clear_launcher_restart_signal,
        consume_launcher_restart_signal,
    )

    clear_launcher_restart_signal()
    current_worker: subprocess.Popen | None = None
    stop_requested = False
    stop_signal: int | None = None

    def _cleanup_current_worker() -> None:
        if current_worker is not None:
            _terminate_worker(current_worker)

    atexit.register(_cleanup_current_worker)

    def _handle_launcher_signal(signum, _frame) -> None:
        nonlocal stop_requested, stop_signal
        if stop_requested:
            _launcher_log(f"received signal {signum} while stopping, exiting launcher")
            raise SystemExit(128 + int(signum))
        stop_requested = True
        stop_signal = int(signum)
        _launcher_log(f"received signal {signum}, scheduling worker shutdown")

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
        if stop_requested:
            raise SystemExit(128 + int(stop_signal or signal.SIGINT))
        worker_env = os.environ.copy()
        worker_env["ZHENXUN_LAUNCHER_PID"] = str(os.getpid())
        worker = subprocess.Popen(
            _build_worker_command(),
            cwd=str(cwd),
            creationflags=_get_worker_creationflags(),
            env=worker_env,
        )
        current_worker = worker
        restart_requested = False
        return_code: int | None = None
        next_restart_check = 0.0
        try:
            while True:
                return_code = worker.poll()
                if return_code is not None:
                    break
                if stop_requested:
                    clear_launcher_restart_signal()
                    _terminate_worker(worker)
                    raise SystemExit(128 + int(stop_signal or signal.SIGINT))
                now = time.monotonic()
                if now >= next_restart_check:
                    next_restart_check = now + RESTART_POLL_INTERVAL
                    if consume_launcher_restart_signal():
                        restart_requested = True
                        _launcher_log(
                            "detected restart request, stopping current worker"
                        )
                        _terminate_worker(worker)
                        return_code = worker.poll()
                        break
                time.sleep(WORKER_POLL_INTERVAL)
        except KeyboardInterrupt:
            clear_launcher_restart_signal()
            _terminate_worker(worker)
            return
        finally:
            if current_worker is worker:
                current_worker = None

        if restart_requested or consume_launcher_restart_signal():
            continue
        raise SystemExit(return_code if return_code is not None else 1)


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
