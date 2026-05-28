from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import inspect
from typing import Any

import nonebot.message as nb_message


@dataclass(frozen=True, slots=True)
class AuthPatchGuardResult:
    ok: bool
    reason: str = ""


_HANDLE_EVENT_PARAMS = {"bot", "event"}
_HANDLE_EVENT_REQUIRED_ATTRS = (
    "escape_tag",
    "logger",
    "NoLogException",
    "AsyncExitStack",
    "_apply_event_preprocessors",
    "_apply_event_postprocessors",
    "TrieRule",
    "matchers",
    "catch",
    "StopPropagation",
    "_handle_exception",
    "anyio",
    "run_coro_with_shield",
)


def _signature_param_names(func: Callable[..., Any]) -> set[str]:
    return set(inspect.signature(func).parameters)


def validate_handle_event_patch() -> AuthPatchGuardResult:
    target = getattr(nb_message, "handle_event", None)
    if target is None:
        return AuthPatchGuardResult(False, "missing nonebot.message.handle_event")
    try:
        params = _signature_param_names(target)
    except Exception as exc:
        return AuthPatchGuardResult(False, f"inspect signature failed: {exc}")
    missing_params = sorted(_HANDLE_EVENT_PARAMS - params)
    if missing_params:
        return AuthPatchGuardResult(
            False,
            "handle_event signature missing params: " + ", ".join(missing_params),
        )
    missing_attrs = [
        attr for attr in _HANDLE_EVENT_REQUIRED_ATTRS if not hasattr(nb_message, attr)
    ]
    if missing_attrs:
        return AuthPatchGuardResult(
            False,
            "nonebot.message missing attrs: " + ", ".join(missing_attrs),
        )
    return AuthPatchGuardResult(True)
