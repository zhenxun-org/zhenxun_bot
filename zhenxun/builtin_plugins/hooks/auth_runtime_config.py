from __future__ import annotations

from dataclasses import dataclass, fields
import os


@dataclass(frozen=True, slots=True)
class AuthDispatchRuntimeConfig:
    hooks_concurrency_limit: int = 5
    db_concurrency_limit: int = 6
    command_exact_limit: int = 96
    command_shortcut_limit: int = 32
    command_regex_limit: int = 8
    system_limit: int = 64
    passive_light_limit: int = 12
    passive_db_limit: int = 4
    passive_http_limit: int = 4
    passive_ai_limit: int = 2
    passive_render_limit: int = 2
    overload_selected_threshold: int = 48
    overload_lane_wait_ms: float = 200.0
    timeout_seconds: float = 5.0
    circuit_reset_time: int = 300
    matcher_route_prefilter_ttl: int = 2
    prefilter_stats_log_interval: float = 10.0
    cache_sweep_interval: float = 1.0
    dispatch_stats_log_interval: float = 10.0


@dataclass(frozen=True, slots=True)
class AuthObservabilityRuntimeConfig:
    buffer_max_retain: int = 20_000
    flush_trigger_size: int = 256
    flush_batch_size: int = 500
    flush_interval_seconds: float = 30.0
    drop_log_interval_seconds: float = 10.0
    allow_sample_rate: float = 0.005
    overloaded_allow_sample_rate: float = 0.02
    non_allow_sample_rate: float = 1.0
    backpressure_sample_rate: float = 0.2
    backpressure_severe_active_threshold: int = 5


_WARNED_ENV_KEYS: set[str] = set()
_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "hooks_concurrency_limit": ("ZX_AUTH_HOOKS_CONCURRENCY_LIMIT",),
    "db_concurrency_limit": ("ZX_AUTH_DB_CONCURRENCY_LIMIT",),
    "command_exact_limit": ("ZX_AUTH_DISPATCH_COMMAND_EXACT_LIMIT",),
    "command_shortcut_limit": ("ZX_AUTH_DISPATCH_COMMAND_SHORTCUT_LIMIT",),
    "command_regex_limit": ("ZX_AUTH_DISPATCH_COMMAND_REGEX_LIMIT",),
    "system_limit": ("ZX_AUTH_DISPATCH_SYSTEM_LIMIT",),
    "passive_light_limit": ("ZX_AUTH_DISPATCH_PASSIVE_LIGHT_LIMIT",),
    "passive_db_limit": ("ZX_AUTH_DISPATCH_PASSIVE_DB_LIMIT",),
    "passive_http_limit": ("ZX_AUTH_DISPATCH_PASSIVE_HTTP_LIMIT",),
    "passive_ai_limit": ("ZX_AUTH_DISPATCH_PASSIVE_AI_LIMIT",),
    "passive_render_limit": ("ZX_AUTH_DISPATCH_PASSIVE_RENDER_LIMIT",),
    "overload_selected_threshold": ("ZX_AUTH_OVERLOAD_SELECTED_THRESHOLD",),
    "overload_lane_wait_ms": ("ZX_AUTH_OVERLOAD_LANE_WAIT_MS",),
    "timeout_seconds": ("ZX_AUTH_TIMEOUT_SECONDS",),
    "circuit_reset_time": ("ZX_AUTH_CIRCUIT_RESET_TIME",),
    "matcher_route_prefilter_ttl": ("ZX_AUTH_MATCHER_ROUTE_PREFILTER_TTL",),
    "prefilter_stats_log_interval": ("ZX_AUTH_PREFILTER_STATS_LOG_INTERVAL",),
    "cache_sweep_interval": ("ZX_AUTH_CACHE_SWEEP_INTERVAL",),
    "dispatch_stats_log_interval": ("ZX_AUTH_DISPATCH_STATS_LOG_INTERVAL",),
}


def _env_name(prefix: str, field_name: str) -> str:
    return f"{prefix}_{field_name.upper()}"


def _env_names(prefix: str, field_name: str) -> tuple[str, ...]:
    generated = _env_name(prefix, field_name)
    aliases = _ENV_ALIASES.get(field_name, ())
    return (*aliases, generated)


def _coerce_env_value(raw: str, default: object) -> object:
    if isinstance(default, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int) and not isinstance(default, bool):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


def _warn_invalid_env(env_name: str, raw: str, exc: Exception) -> None:
    if env_name in _WARNED_ENV_KEYS:
        return
    _WARNED_ENV_KEYS.add(env_name)
    try:
        from zhenxun.services.log import logger

        logger.warning(
            f"{env_name}={raw!r} 解析失败，使用默认值: {exc}",
            "AuthRuntimeConfig",
        )
    except Exception:
        # Config is imported early on the auth hot path; logging must be optional.
        return


def _load_config(cls: type, prefix: str):
    values = {}
    default_obj = cls()
    for item in fields(default_obj):
        default = getattr(default_obj, item.name)
        env_name = ""
        raw = None
        for candidate in _env_names(prefix, item.name):
            candidate_value = os.getenv(candidate)
            if candidate_value is not None and candidate_value.strip():
                env_name = candidate
                raw = candidate_value
                break
        if raw is None or not raw.strip():
            values[item.name] = default
            continue
        try:
            values[item.name] = _coerce_env_value(raw, default)
        except Exception as exc:
            _warn_invalid_env(env_name, raw, exc)
            values[item.name] = default
    return cls(**values)


AUTH_DISPATCH_RUNTIME_CONFIG = _load_config(
    AuthDispatchRuntimeConfig,
    "ZX_AUTH",
)
AUTH_OBSERVABILITY_RUNTIME_CONFIG = _load_config(
    AuthObservabilityRuntimeConfig,
    "ZX_AUTH_OBSERVABILITY",
)
