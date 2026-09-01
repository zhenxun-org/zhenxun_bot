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
ENV_EXAMPLE_FILE = ".env.example"
ENV_DEV_FILE = ".env.dev"


def _env_assignment_key(line: str, *, include_commented: bool = False) -> str | None:
    stripped = line.strip()
    if include_commented and stripped.startswith("#"):
        stripped = stripped[1:].lstrip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    return key if key.replace("_", "").isalnum() else None


def _env_key(line: str) -> str | None:
    return _env_assignment_key(line)


def _env_block_key(block: list[str]) -> str | None:
    for line in block:
        if key := _env_key(line):
            return key
    return None


def _env_block_anchor_key(block: list[str]) -> str | None:
    for line in block:
        if key := _env_assignment_key(line, include_commented=True):
            return key
    return None


def _split_env_blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start_index = 0
    for index, line in enumerate(lines):
        if line.strip():
            if not current:
                start_index = index
            current.append(line)
        elif current:
            blocks.append((start_index, current))
            current = []

    if current:
        blocks.append((start_index, current))
    return blocks


def _find_env_block_start(lines: list[str], key: str) -> int | None:
    for start_index, block in _split_env_blocks(lines):
        if _env_block_anchor_key(block) == key:
            return start_index
    return None


def _insert_env_block_before(
    lines: list[str],
    index: int,
    block: list[str],
) -> list[str]:
    insert_block = block.copy()
    if index > 0 and lines[index - 1].strip():
        insert_block.insert(0, "\n")
    if index < len(lines) and insert_block and insert_block[-1].strip():
        insert_block.append("\n")
    return lines[:index] + insert_block + lines[index:]


def _sync_env_missing_items(project_root: Path) -> None:
    """Copy missing .env keys from .env.example without touching existing values."""
    example_path = project_root / ENV_EXAMPLE_FILE
    env_path = project_root / ENV_DEV_FILE
    if not example_path.exists():
        return
    if not env_path.exists():
        env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        _launcher_log("已根据 .env.example 生成 .env.dev")
        return

    example_lines = example_path.read_text(encoding="utf-8").splitlines(keepends=True)
    env_lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    example_blocks = _split_env_blocks(example_lines)
    existing_keys = {key for line in env_lines if (key := _env_key(line))}
    missing_blocks: list[tuple[int, list[str]]] = []

    for block_index, (_, block) in enumerate(example_blocks):
        key = _env_block_key(block)
        if key and key not in existing_keys:
            missing_blocks.append((block_index, block))

    if not missing_blocks:
        return

    updated_lines = env_lines
    added_keys: list[str] = []
    for block_index, block in missing_blocks:
        key = _env_block_key(block)
        if not key:
            continue
        anchor_index = len(updated_lines)
        for _, next_block in example_blocks[block_index + 1 :]:
            next_key = _env_block_anchor_key(next_block)
            if not next_key:
                continue
            if (found := _find_env_block_start(updated_lines, next_key)) is not None:
                anchor_index = found
                break
        updated_lines = _insert_env_block_before(updated_lines, anchor_index, block)
        existing_keys.add(key)
        added_keys.append(key)

    env_path.write_text("".join(updated_lines), encoding="utf-8")
    _launcher_log(f"已补齐 .env.dev 缺失配置: {', '.join(added_keys)}")


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
    project_root = _ensure_project_root()
    _sync_env_missing_items(project_root)

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

    nonebot.init(htmlrender_browser_channel=htmlrender_browser_channel)

    from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

    from zhenxun.configs.config import BotConfig

    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)
    enabled_adapters = ["OneBot V11"]

    if BotConfig.qq_adapter_load:
        try:
            from nonebot.adapters.qq import Adapter as QQAdapter  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "QQ_ADAPTER_LOAD=True 但未安装 nonebot-adapter-qq，"
                "请安装后再开启 QQ 官方适配器。"
            ) from e
        driver.register_adapter(QQAdapter)
        enabled_adapters.append("QQ")

    nonebot.logger.info(f"已启用适配器: {', '.join(enabled_adapters)}")

    nonebot.load_plugins("zhenxun/builtin_plugins")
    nonebot.load_plugins("zhenxun/plugins")

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
