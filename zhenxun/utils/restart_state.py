from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_RESTART_STATE_FILE = Path() / "data" / ".restart_state.json"
_LAUNCHER_ACTION_KEY = "launcher_action"
_ACTION_RESTART = "restart"


def _ensure_state_parent() -> None:
    _RESTART_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


def read_restart_state() -> dict[str, Any]:
    if not _RESTART_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(_RESTART_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_restart_state(state: dict[str, Any]) -> None:
    if not state:
        if _RESTART_STATE_FILE.exists():
            _RESTART_STATE_FILE.unlink()
        return
    _ensure_state_parent()
    temp_file = _RESTART_STATE_FILE.with_name(f"{_RESTART_STATE_FILE.name}.tmp")
    temp_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(_RESTART_STATE_FILE)


def consume_launcher_restart_signal() -> bool:
    state = read_restart_state()
    if state.get(_LAUNCHER_ACTION_KEY) != _ACTION_RESTART:
        return False
    state.pop(_LAUNCHER_ACTION_KEY, None)
    write_restart_state(state)
    return True


def clear_launcher_restart_signal() -> None:
    state = read_restart_state()
    if _LAUNCHER_ACTION_KEY not in state:
        return
    state.pop(_LAUNCHER_ACTION_KEY, None)
    write_restart_state(state)
