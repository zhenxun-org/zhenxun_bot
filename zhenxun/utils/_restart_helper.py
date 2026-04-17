from __future__ import annotations

import argparse
import contextlib
import os
from pathlib import Path
import socket
import subprocess
import time

import psutil

_PARENT_POLL_INTERVAL = 0.2
_PORT_POLL_INTERVAL = 0.2
_PORT_WAIT_TIMEOUT = 30.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="zhenxun restart helper")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--parent-create-time", type=float, required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing restart command")
    return args


def _is_same_process(pid: int, create_time: float) -> bool:
    try:
        process = psutil.Process(pid)
        return abs(process.create_time() - create_time) < 1e-3
    except psutil.Error:
        return False


def _wait_for_parent_exit(pid: int, create_time: float) -> None:
    while _is_same_process(pid, create_time):
        time.sleep(_PARENT_POLL_INTERVAL)


def _can_bind(host: str, port: int) -> bool:
    if not host or port <= 0:
        return True
    try:
        addr_infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return True
    for family, socktype, proto, _, sockaddr in addr_infos:
        with contextlib.closing(socket.socket(family, socktype, proto)) as sock:
            try:
                if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_EXCLUSIVEADDRUSE,
                        1,
                    )
                sock.bind(sockaddr)
                return True
            except OSError:
                continue
    return False


def _wait_for_port_release(host: str, port: int) -> None:
    if not host or port <= 0:
        return
    deadline = time.monotonic() + _PORT_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        if _can_bind(host, port):
            return
        time.sleep(_PORT_POLL_INTERVAL)


def _get_creationflags() -> int:
    creationflags = 0
    for flag_name in (
        "CREATE_NEW_PROCESS_GROUP",
        "DETACHED_PROCESS",
        "CREATE_NO_WINDOW",
    ):
        creationflags |= getattr(subprocess, flag_name, 0)
    return creationflags


def _spawn_target(command: list[str], cwd: str) -> int:
    try:
        if os.name == "nt":
            subprocess.Popen(
                command,
                cwd=cwd,
                close_fds=True,
                creationflags=_get_creationflags(),
            )
        else:
            subprocess.Popen(
                command,
                cwd=cwd,
                close_fds=True,
                start_new_session=True,
            )
    except Exception:
        return 1
    return 0


def main() -> int:
    args = _parse_args()
    cwd = str(Path(args.cwd).resolve())
    _wait_for_parent_exit(args.parent_pid, args.parent_create_time)
    _wait_for_port_release(args.host, args.port)
    return _spawn_target(args.command, cwd)


if __name__ == "__main__":
    raise SystemExit(main())
