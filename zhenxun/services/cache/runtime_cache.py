from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import os
import time
from typing import TYPE_CHECKING, Any, ClassVar
import uuid

from zhenxun.services.cache.config import CacheMode
from zhenxun.services.log import logger
from zhenxun.services.message_load import is_db_unhealthy
from zhenxun.utils.enum import (
    BlockType,
    LimitCheckType,
    LimitWatchType,
    PluginLimitType,
    PluginType,
)
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

if TYPE_CHECKING:
    from zhenxun.models.plugin_info import PluginInfo

LOG_COMMAND = "RuntimeCache"


def _coerce_int(value, default: int) -> int:
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        return default
    return value_int if value_int >= 0 else default


# RuntimeCache 以模型 save/delete 主动失效为主，周期 refresh 只做兜底。
# 这些默认值避免低压力运行时频繁全量扫表。
# 权限检查热路径以 RuntimeCache/AuthSnapshot 为唯一数据入口；普通业务的
# DataAccess/CacheRoot 缓存不能替代这里的运行态快照。
PLUGININFO_MEM_REFRESH_INTERVAL = 3600  # 60分钟
BAN_MEM_REFRESH_INTERVAL = 300
BAN_MEM_CLEAN_INTERVAL = 60
BAN_MEM_CLEANUP_DB = True
BAN_MEM_NEGATIVE_TTL = 5
BOT_MEM_REFRESH_INTERVAL = 900  # 15分钟
BOT_MEM_NEGATIVE_TTL = 60
GROUP_MEM_REFRESH_INTERVAL = 900  # 15分钟
GROUP_MEM_NEGATIVE_TTL = 60
LEVEL_MEM_REFRESH_INTERVAL = 900  # 15分钟
LEVEL_MEM_NEGATIVE_TTL = 60
TASK_MEM_REFRESH_INTERVAL = 1800
TASK_MEM_NEGATIVE_TTL = 60
LIMIT_MEM_REFRESH_INTERVAL = 900  # 15分钟
LIMIT_MEM_NEGATIVE_TTL = 30
RUNTIME_CACHE_SYNC_ENABLED = True
RUNTIME_CACHE_SYNC_CHANNEL = "ZHENXUN_RUNTIME_CACHE_SYNC"
RUNTIME_CACHE_LOAD_RETRY_SECONDS = 1.0
RUNTIME_CACHE_STARTUP_REFRESH_SKIP_SECONDS = 5.0
RUNTIME_CACHE_DB_TIMEOUT_SECONDS = 3.0


INSTANCE_ID = uuid.uuid4().hex
_CACHE_READY_EVENT = asyncio.Event()
_APPLYING_REMOTE_CACHE_EVENT: ContextVar[bool] = ContextVar(
    "APPLYING_REMOTE_RUNTIME_CACHE_EVENT",
    default=False,
)


def _env_get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        value = os.getenv(name.lower())
    return value if value is not None else default


def _redis_enabled() -> bool:
    mode = (_env_get("CACHE_MODE") or "").upper()
    if mode != CacheMode.REDIS:
        return False
    return bool(_env_get("REDIS_HOST"))


def is_cache_ready() -> bool:
    return _CACHE_READY_EVENT.is_set()


async def wait_cache_ready(timeout: float | None = None) -> bool:
    try:
        await asyncio.wait_for(_CACHE_READY_EVENT.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


def _parse_block_modules(value: str) -> frozenset[str]:
    if not value:
        return frozenset()
    items = []
    for part in value.split("<"):
        part = part.strip()
        if not part:
            continue
        part = part.strip(",").strip()
        if part:
            items.append(part)
    return frozenset(items)


@dataclass(frozen=True)
class PluginInfoSnapshot:
    id: int
    module: str
    module_path: str
    name: str
    status: bool
    block_type: BlockType | None
    load_status: bool
    author: str | None
    version: str | None
    level: int
    default_status: bool
    limit_superuser: bool
    menu_type: str
    plugin_type: PluginType | None
    cost_gold: int
    admin_level: int | None
    ignore_prompt: bool
    is_delete: bool
    parent: str | None
    is_show: bool
    ignore_statistics: bool
    impression: float

    @classmethod
    def from_model(cls, model) -> "PluginInfoSnapshot":
        return cls(
            id=int(getattr(model, "id", 0) or 0),
            module=str(getattr(model, "module", "") or ""),
            module_path=str(getattr(model, "module_path", "") or ""),
            name=str(getattr(model, "name", "") or ""),
            status=bool(getattr(model, "status", True)),
            block_type=getattr(model, "block_type", None),
            load_status=bool(getattr(model, "load_status", True)),
            author=getattr(model, "author", None),
            version=getattr(model, "version", None),
            level=int(getattr(model, "level", 0) or 0),
            default_status=bool(getattr(model, "default_status", True)),
            limit_superuser=bool(getattr(model, "limit_superuser", False)),
            menu_type=str(getattr(model, "menu_type", "") or ""),
            plugin_type=getattr(model, "plugin_type", None),
            cost_gold=int(getattr(model, "cost_gold", 0) or 0),
            admin_level=getattr(model, "admin_level", None),
            ignore_prompt=bool(getattr(model, "ignore_prompt", False)),
            is_delete=bool(getattr(model, "is_delete", False)),
            parent=getattr(model, "parent", None),
            is_show=bool(getattr(model, "is_show", True)),
            ignore_statistics=bool(getattr(model, "ignore_statistics", False)),
            impression=float(getattr(model, "impression", 0) or 0),
        )

    def to_model(self):
        from zhenxun.models.plugin_info import PluginInfo

        plugin = PluginInfo(
            id=self.id,
            module=self.module,
            module_path=self.module_path,
            name=self.name,
            status=self.status,
            block_type=self.block_type,
            load_status=self.load_status,
            author=self.author,
            version=self.version,
            level=self.level,
            default_status=self.default_status,
            limit_superuser=self.limit_superuser,
            menu_type=self.menu_type,
            plugin_type=self.plugin_type,
            cost_gold=self.cost_gold,
            admin_level=self.admin_level,
            ignore_prompt=self.ignore_prompt,
            is_delete=self.is_delete,
            parent=self.parent,
            is_show=self.is_show,
            ignore_statistics=self.ignore_statistics,
            impression=self.impression,
        )
        plugin._saved_in_db = True
        return plugin

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module,
            "module_path": self.module_path,
            "name": self.name,
            "status": self.status,
            "block_type": self.block_type.value if self.block_type else None,
            "load_status": self.load_status,
            "author": self.author,
            "version": self.version,
            "level": self.level,
            "default_status": self.default_status,
            "limit_superuser": self.limit_superuser,
            "menu_type": self.menu_type,
            "plugin_type": self.plugin_type.value if self.plugin_type else None,
            "cost_gold": self.cost_gold,
            "admin_level": self.admin_level,
            "ignore_prompt": self.ignore_prompt,
            "is_delete": self.is_delete,
            "parent": self.parent,
            "is_show": self.is_show,
            "ignore_statistics": self.ignore_statistics,
            "impression": self.impression,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PluginInfoSnapshot":
        block_type = payload.get("block_type")
        if block_type is not None and not isinstance(block_type, BlockType):
            block_type = BlockType(block_type)
        plugin_type = payload.get("plugin_type")
        if plugin_type is not None and not isinstance(plugin_type, PluginType):
            plugin_type = PluginType(plugin_type)
        return cls(
            id=int(payload.get("id", 0) or 0),
            module=str(payload.get("module", "") or ""),
            module_path=str(payload.get("module_path", "") or ""),
            name=str(payload.get("name", "") or ""),
            status=bool(payload.get("status", True)),
            block_type=block_type,
            load_status=bool(payload.get("load_status", True)),
            author=payload.get("author"),
            version=payload.get("version"),
            level=int(payload.get("level", 0) or 0),
            default_status=bool(payload.get("default_status", True)),
            limit_superuser=bool(payload.get("limit_superuser", False)),
            menu_type=str(payload.get("menu_type", "") or ""),
            plugin_type=plugin_type,
            cost_gold=int(payload.get("cost_gold", 0) or 0),
            admin_level=payload.get("admin_level"),
            ignore_prompt=bool(payload.get("ignore_prompt", False)),
            is_delete=bool(payload.get("is_delete", False)),
            parent=payload.get("parent"),
            is_show=bool(payload.get("is_show", True)),
            ignore_statistics=bool(payload.get("ignore_statistics", False)),
            impression=float(payload.get("impression", 0) or 0),
        )


@dataclass(frozen=True)
class BanEntry:
    user_id: str | None
    group_id: str | None
    ban_level: int
    ban_time: int
    duration: int
    expire_at: float | None

    def remaining(self, now: float | None = None) -> int:
        if self.duration == -1:
            return -1
        now_ts = time.time() if now is None else now
        left = int(self.ban_time + self.duration - now_ts)
        return left if left > 0 else 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "group_id": self.group_id,
            "ban_level": self.ban_level,
            "ban_time": self.ban_time,
            "duration": self.duration,
            "expire_at": self.expire_at,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BanEntry":
        user_id = payload.get("user_id")
        group_id = payload.get("group_id")
        ban_time = int(payload.get("ban_time", 0) or 0)
        duration = int(payload.get("duration", 0) or 0)
        expire_at = payload.get("expire_at")
        if expire_at is None and duration != -1:
            expire_at = float(ban_time + duration)
        return cls(
            user_id=str(user_id) if user_id else None,
            group_id=str(group_id) if group_id else None,
            ban_level=int(payload.get("ban_level", 0) or 0),
            ban_time=ban_time,
            duration=duration,
            expire_at=expire_at,
        )


@dataclass(frozen=True)
class BotSnapshot:
    bot_id: str
    status: bool
    platform: str | None
    block_plugins: str
    block_tasks: str
    available_plugins: str
    available_tasks: str

    @classmethod
    def from_model(cls, model) -> "BotSnapshot":
        return cls(
            bot_id=str(model.bot_id),
            status=bool(model.status),
            platform=getattr(model, "platform", None),
            block_plugins=getattr(model, "block_plugins", "") or "",
            block_tasks=getattr(model, "block_tasks", "") or "",
            available_plugins=getattr(model, "available_plugins", "") or "",
            available_tasks=getattr(model, "available_tasks", "") or "",
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "status": self.status,
            "platform": self.platform,
            "block_plugins": self.block_plugins,
            "block_tasks": self.block_tasks,
            "available_plugins": self.available_plugins,
            "available_tasks": self.available_tasks,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BotSnapshot":
        return cls(
            bot_id=str(payload.get("bot_id", "")),
            status=bool(payload.get("status", True)),
            platform=payload.get("platform"),
            block_plugins=payload.get("block_plugins", "") or "",
            block_tasks=payload.get("block_tasks", "") or "",
            available_plugins=payload.get("available_plugins", "") or "",
            available_tasks=payload.get("available_tasks", "") or "",
        )


@dataclass(frozen=True)
class GroupSnapshot:
    group_id: str
    channel_id: str | None
    group_name: str
    max_member_count: int
    member_count: int
    status: bool
    level: int
    is_super: bool
    group_flag: int
    block_plugin: str
    superuser_block_plugin: str
    block_task: str
    superuser_block_task: str
    platform: str | None
    block_plugin_set: frozenset[str] = field(default_factory=frozenset)
    superuser_block_plugin_set: frozenset[str] = field(default_factory=frozenset)
    block_task_set: frozenset[str] = field(default_factory=frozenset)
    superuser_block_task_set: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_model(cls, model) -> "GroupSnapshot":
        block_plugin = getattr(model, "block_plugin", "") or ""
        superuser_block_plugin = getattr(model, "superuser_block_plugin", "") or ""
        block_task = getattr(model, "block_task", "") or ""
        superuser_block_task = getattr(model, "superuser_block_task", "") or ""
        return cls(
            group_id=str(model.group_id),
            channel_id=getattr(model, "channel_id", None),
            group_name=getattr(model, "group_name", "") or "",
            max_member_count=int(getattr(model, "max_member_count", 0) or 0),
            member_count=int(getattr(model, "member_count", 0) or 0),
            status=bool(getattr(model, "status", True)),
            level=int(getattr(model, "level", 0) or 0),
            is_super=bool(getattr(model, "is_super", False)),
            group_flag=int(getattr(model, "group_flag", 0) or 0),
            block_plugin=block_plugin,
            superuser_block_plugin=superuser_block_plugin,
            block_task=block_task,
            superuser_block_task=superuser_block_task,
            platform=getattr(model, "platform", None),
            block_plugin_set=_parse_block_modules(block_plugin),
            superuser_block_plugin_set=_parse_block_modules(superuser_block_plugin),
            block_task_set=_parse_block_modules(block_task),
            superuser_block_task_set=_parse_block_modules(superuser_block_task),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "channel_id": self.channel_id,
            "group_name": self.group_name,
            "max_member_count": self.max_member_count,
            "member_count": self.member_count,
            "status": self.status,
            "level": self.level,
            "is_super": self.is_super,
            "group_flag": self.group_flag,
            "block_plugin": self.block_plugin,
            "superuser_block_plugin": self.superuser_block_plugin,
            "block_task": self.block_task,
            "superuser_block_task": self.superuser_block_task,
            "platform": self.platform,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "GroupSnapshot":
        block_plugin = payload.get("block_plugin", "") or ""
        superuser_block_plugin = payload.get("superuser_block_plugin", "") or ""
        block_task = payload.get("block_task", "") or ""
        superuser_block_task = payload.get("superuser_block_task", "") or ""
        return cls(
            group_id=str(payload.get("group_id", "")),
            channel_id=payload.get("channel_id"),
            group_name=payload.get("group_name", "") or "",
            max_member_count=int(payload.get("max_member_count", 0) or 0),
            member_count=int(payload.get("member_count", 0) or 0),
            status=bool(payload.get("status", True)),
            level=int(payload.get("level", 0) or 0),
            is_super=bool(payload.get("is_super", False)),
            group_flag=int(payload.get("group_flag", 0) or 0),
            block_plugin=block_plugin,
            superuser_block_plugin=superuser_block_plugin,
            block_task=block_task,
            superuser_block_task=superuser_block_task,
            platform=payload.get("platform"),
            block_plugin_set=_parse_block_modules(block_plugin),
            superuser_block_plugin_set=_parse_block_modules(superuser_block_plugin),
            block_task_set=_parse_block_modules(block_task),
            superuser_block_task_set=_parse_block_modules(superuser_block_task),
        )


@dataclass(frozen=True)
class LevelUserSnapshot:
    user_id: str
    group_id: str | None
    user_level: int
    group_flag: int

    @classmethod
    def from_model(cls, model) -> "LevelUserSnapshot":
        return cls(
            user_id=str(model.user_id),
            group_id=getattr(model, "group_id", None),
            user_level=int(getattr(model, "user_level", 0) or 0),
            group_flag=int(getattr(model, "group_flag", 0) or 0),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "group_id": self.group_id,
            "user_level": self.user_level,
            "group_flag": self.group_flag,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LevelUserSnapshot":
        return cls(
            user_id=str(payload.get("user_id", "")),
            group_id=payload.get("group_id"),
            user_level=int(payload.get("user_level", 0) or 0),
            group_flag=int(payload.get("group_flag", 0) or 0),
        )


@dataclass(frozen=True)
class PluginLimitSnapshot:
    id: int
    module: str
    module_path: str
    limit_type: PluginLimitType
    watch_type: LimitWatchType
    check_type: LimitCheckType
    status: bool
    result: str | None
    cd: int | None
    max_count: int | None

    @classmethod
    def from_model(cls, model) -> "PluginLimitSnapshot":
        return cls(
            id=int(model.id),
            module=str(model.module),
            module_path=str(model.module_path),
            limit_type=model.limit_type,
            watch_type=model.watch_type,
            check_type=model.check_type,
            status=bool(model.status),
            result=getattr(model, "result", None),
            cd=getattr(model, "cd", None),
            max_count=getattr(model, "max_count", None),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module,
            "module_path": self.module_path,
            "limit_type": self.limit_type.value,
            "watch_type": self.watch_type.value,
            "check_type": self.check_type.value,
            "status": self.status,
            "result": self.result,
            "cd": self.cd,
            "max_count": self.max_count,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PluginLimitSnapshot":
        return cls(
            id=int(payload.get("id", 0) or 0),
            module=str(payload.get("module", "")),
            module_path=str(payload.get("module_path", "")),
            limit_type=PluginLimitType(payload.get("limit_type", PluginLimitType.CD)),
            watch_type=LimitWatchType(payload.get("watch_type", LimitWatchType.USER)),
            check_type=LimitCheckType(payload.get("check_type", LimitCheckType.ALL)),
            status=bool(payload.get("status", True)),
            result=payload.get("result"),
            cd=payload.get("cd"),
            max_count=payload.get("max_count"),
        )


@dataclass(frozen=True)
class TaskInfoSnapshot:
    id: int
    module: str
    name: str
    status: bool
    load_status: bool
    default_status: bool
    run_time: str | None

    @classmethod
    def from_model(cls, model) -> "TaskInfoSnapshot":
        return cls(
            id=int(getattr(model, "id", 0) or 0),
            module=str(model.module),
            name=str(getattr(model, "name", "") or ""),
            status=bool(getattr(model, "status", True)),
            load_status=bool(getattr(model, "load_status", True)),
            default_status=bool(getattr(model, "default_status", True)),
            run_time=getattr(model, "run_time", None),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module,
            "name": self.name,
            "status": self.status,
            "load_status": self.load_status,
            "default_status": self.default_status,
            "run_time": self.run_time,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TaskInfoSnapshot":
        return cls(
            id=int(payload.get("id", 0) or 0),
            module=str(payload.get("module", "")),
            name=str(payload.get("name", "") or ""),
            status=bool(payload.get("status", True)),
            load_status=bool(payload.get("load_status", True)),
            default_status=bool(payload.get("default_status", True)),
            run_time=payload.get("run_time"),
        )


class RuntimeCacheSync:
    _redis: ClassVar[Any | None] = None
    _pubsub: ClassVar[Any | None] = None
    _task: ClassVar[asyncio.Task | None] = None
    _publish_tasks: ClassVar[set[asyncio.Task]] = set()
    _ready: ClassVar[bool] = False
    _channel: ClassVar[str] = ""

    @classmethod
    def _sync_enabled(cls) -> bool:
        enabled = RUNTIME_CACHE_SYNC_ENABLED
        return enabled and _redis_enabled()

    @classmethod
    async def start(cls) -> None:
        if cls._ready:
            return
        if not cls._sync_enabled():
            return
        try:
            import redis.asyncio as redis_async
        except ImportError:
            logger.warning(
                "redis not installed, runtime cache sync disabled", LOG_COMMAND
            )
            return

        host = _env_get("REDIS_HOST")
        if not host:
            return
        port = _coerce_int(_env_get("REDIS_PORT"), 6379)
        password = _env_get("REDIS_PASSWORD")
        cls._channel = RUNTIME_CACHE_SYNC_CHANNEL
        try:
            cls._redis = redis_async.Redis(
                host=host,
                port=port,
                password=password,
                decode_responses=True,
            )
            cls._pubsub = cls._redis.pubsub()
            if cls._pubsub is None:
                return
            await cls._pubsub.subscribe(cls._channel)
            cls._task = asyncio.create_task(cls._listen_loop())
            cls._ready = True
            logger.info("runtime cache sync enabled", LOG_COMMAND)
        except Exception as exc:
            logger.error("runtime cache sync init failed", LOG_COMMAND, e=exc)
            await cls.stop()

    @classmethod
    async def stop(cls) -> None:
        cls._ready = False
        if cls._publish_tasks:
            tasks = list(cls._publish_tasks)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                for task in tasks:
                    if not task.done():
                        task.cancel()
            finally:
                cls._publish_tasks.difference_update(tasks)
        if cls._task and not cls._task.done():
            cls._task.cancel()
        cls._task = None
        try:
            if cls._pubsub is not None:
                await cls._pubsub.close()
        except Exception:
            pass
        cls._pubsub = None
        try:
            if cls._redis is not None:
                await cls._redis.close()
        except Exception:
            pass
        cls._redis = None

    @classmethod
    def publish_event(cls, cache_type: str, action: str, data: dict[str, Any]) -> None:
        if not cls._ready:
            return
        payload = {
            "source": INSTANCE_ID,
            "type": cache_type,
            "action": action,
            "data": data,
        }
        task = asyncio.create_task(cls._publish(payload))
        cls._publish_tasks.add(task)
        task.add_done_callback(cls._publish_tasks.discard)

    @classmethod
    async def _publish(cls, payload: dict[str, Any]) -> None:
        if not cls._ready or cls._redis is None:
            return
        try:
            await cls._redis.publish(cls._channel, json.dumps(payload))
        except Exception as exc:
            logger.error("runtime cache sync publish failed", LOG_COMMAND, e=exc)

    @classmethod
    async def _listen_loop(cls) -> None:
        if cls._pubsub is None:
            return
        try:
            while True:
                message = await cls._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if not message:
                    await asyncio.sleep(0.05)
                    continue
                await cls._handle_message(message.get("data"))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("runtime cache sync listener failed", LOG_COMMAND, e=exc)

    @classmethod
    async def _handle_message(cls, raw: Any) -> None:
        if raw is None:
            return
        if isinstance(raw, bytes | bytearray):
            try:
                raw = raw.decode()
            except Exception:
                return
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if payload.get("source") == INSTANCE_ID:
            return
        cache_type = payload.get("type")
        action = payload.get("action")
        data = payload.get("data") or {}
        token = _APPLYING_REMOTE_CACHE_EVENT.set(True)
        try:
            if cache_type == "bot":
                await BotMemoryCache.apply_sync_event(action, data)
            elif cache_type == "group":
                await GroupMemoryCache.apply_sync_event(action, data)
            elif cache_type == "ban":
                await BanMemoryCache.apply_sync_event(action, data)
            elif cache_type == "level":
                await LevelUserMemoryCache.apply_sync_event(action, data)
            elif cache_type == "task":
                await TaskInfoMemoryCache.apply_sync_event(action, data)
            elif cache_type == "plugin_limit":
                await PluginLimitMemoryCache.apply_sync_event(action, data)
            elif cache_type == "plugin":
                await PluginInfoMemoryCache.apply_sync_event(action, data)
        finally:
            _APPLYING_REMOTE_CACHE_EVENT.reset(token)


class RuntimeCacheMutation:
    """Small helpers for runtime cache mutation bookkeeping.

    Cache classes still own their storage layout. This helper centralizes the
    shared mutation side effects: health markers, negative-cache cleanup and
    cross-process publish.
    """

    _load_locks: ClassVar[dict[str, asyncio.Lock]] = {}
    _retry_after: ClassVar[dict[str, float]] = {}

    @classmethod
    async def ensure_loaded(cls, cache_cls: type, label: str) -> None:
        if is_db_unhealthy():
            cls.mark_error(cache_cls, RuntimeError("database unhealthy"))
            return
        if getattr(cache_cls, "_loaded", False):
            return
        now = time.monotonic()
        if cls._retry_after.get(label, 0.0) > now:
            return
        lock = cls._load_locks.setdefault(label, asyncio.Lock())
        async with lock:
            if is_db_unhealthy():
                cls.mark_error(cache_cls, RuntimeError("database unhealthy"))
                return
            if getattr(cache_cls, "_loaded", False):
                return
            now = time.monotonic()
            if cls._retry_after.get(label, 0.0) > now:
                return
            try:
                await cache_cls.refresh()
            except Exception as exc:
                cls.mark_error(cache_cls, exc)
                cls._retry_after[label] = (
                    time.monotonic() + RUNTIME_CACHE_LOAD_RETRY_SECONDS
                )
                return

    @staticmethod
    async def read_db(cache_cls: type, coro, *, operation: str):
        from zhenxun.services.db_context import with_db_timeout

        if is_db_unhealthy():
            RuntimeCacheMutation.mark_error(
                cache_cls, RuntimeError("database unhealthy")
            )
            return None
        try:
            return await with_db_timeout(
                coro,
                timeout=RUNTIME_CACHE_DB_TIMEOUT_SECONDS,
                operation=operation,
                source="runtime_cache",
            )
        except Exception as exc:
            RuntimeCacheMutation.mark_error(cache_cls, exc)
            return None

    @staticmethod
    def mark_refreshed(cache_cls: type) -> None:
        setattr(cache_cls, "_loaded", True)
        setattr(cache_cls, "_last_refresh", time.time())
        setattr(cache_cls, "_last_error", None)

    @staticmethod
    def mark_error(cache_cls: type, exc: Exception) -> None:
        setattr(cache_cls, "_last_error", f"{type(exc).__name__}: {exc}")

    @staticmethod
    def clear_negative_key(cache_cls: type, key: object) -> None:
        negative = getattr(cache_cls, "_negative", None)
        if isinstance(negative, dict):
            negative.pop(key, None)

    @staticmethod
    def clear_negative_all(cache_cls: type) -> None:
        negative = getattr(cache_cls, "_negative", None)
        if isinstance(negative, dict):
            negative.clear()

    @staticmethod
    def publish(cache_type: str, action: str, data: dict[str, Any]) -> None:
        if _APPLYING_REMOTE_CACHE_EVENT.get():
            return
        RuntimeCacheSync.publish_event(cache_type, action, data)


class PluginInfoMemoryCache:
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _by_module: ClassVar[dict[str, PluginInfoSnapshot]] = {}
    _by_module_path: ClassVar[dict[str, PluginInfoSnapshot]] = {}
    _loaded: ClassVar[bool] = False
    _refresh_task: ClassVar[asyncio.Task | None] = None
    _last_refresh: ClassVar[float] = 0.0
    _last_error: ClassVar[str | None] = None

    @classmethod
    def _to_model(cls, snapshot: PluginInfoSnapshot | None) -> "PluginInfo | None":
        return snapshot.to_model() if snapshot else None

    @classmethod
    def _store_snapshot(cls, snapshot: PluginInfoSnapshot) -> None:
        if snapshot.module:
            old = cls._by_module.get(snapshot.module)
            if old and old.module_path != snapshot.module_path:
                cls._by_module_path.pop(old.module_path, None)
            cls._by_module[snapshot.module] = snapshot
        if snapshot.module_path:
            old = cls._by_module_path.get(snapshot.module_path)
            if old and old.module != snapshot.module:
                cls._by_module.pop(old.module, None)
            cls._by_module_path[snapshot.module_path] = snapshot

    @staticmethod
    def _module_snapshot_rank(snapshot: PluginInfoSnapshot) -> tuple[int, int]:
        return (1 if snapshot.load_status else 0, snapshot.id)

    @classmethod
    async def refresh(cls) -> None:
        from zhenxun.models.plugin_info import PluginInfo

        async with cls._lock:
            plugins = await RuntimeCacheMutation.read_db(
                cls,
                PluginInfo.all(),
                operation="PluginInfoMemoryCache.refresh",
            )
            if plugins is None:
                return
            by_module: dict[str, PluginInfoSnapshot] = {}
            by_module_path: dict[str, PluginInfoSnapshot] = {}
            for plugin in plugins:
                snapshot = PluginInfoSnapshot.from_model(plugin)
                if snapshot.module:
                    current = by_module.get(snapshot.module)
                    if current is None or cls._module_snapshot_rank(
                        snapshot
                    ) >= cls._module_snapshot_rank(current):
                        by_module[snapshot.module] = snapshot
                if snapshot.module_path:
                    by_module_path[snapshot.module_path] = snapshot
            cls._by_module = by_module
            cls._by_module_path = by_module_path
            RuntimeCacheMutation.mark_refreshed(cls)
            logger.debug(
                f"plugin cache refreshed: {len(by_module)} entries", LOG_COMMAND
            )

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await RuntimeCacheMutation.ensure_loaded(cls, "plugin")

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._loaded

    @classmethod
    async def get_by_module(cls, module: str) -> "PluginInfo | None":
        if not cls._loaded:
            await cls.ensure_loaded()
        return cls._to_model(cls._by_module.get(module))

    @classmethod
    def get_by_module_if_ready(cls, module: str) -> "PluginInfo | None":
        if not cls._loaded:
            return None
        return cls._to_model(cls._by_module.get(module))

    @classmethod
    async def get_all(cls) -> dict[str, "PluginInfo"]:
        if not cls._loaded:
            await cls.ensure_loaded()
        return {
            module: snapshot.to_model() for module, snapshot in cls._by_module.items()
        }

    @classmethod
    def get_by_module_path(cls, module_path: str) -> "PluginInfo | None":
        return cls._to_model(cls._by_module_path.get(module_path))

    @classmethod
    def set_plugin(cls, plugin) -> None:
        if not plugin:
            return
        snapshot = PluginInfoSnapshot.from_model(plugin)
        cls._store_snapshot(snapshot)

    @classmethod
    def remove_by_module(cls, module: str) -> None:
        snapshot = cls._by_module.pop(module, None)
        if snapshot and snapshot.module_path:
            cls._by_module_path.pop(snapshot.module_path, None)

    @classmethod
    async def upsert_from_model(cls, plugin) -> None:
        if not plugin:
            return
        async with cls._lock:
            snapshot = PluginInfoSnapshot.from_model(plugin)
            cls._store_snapshot(snapshot)
        RuntimeCacheMutation.publish("plugin", "upsert", snapshot.to_payload())

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        try:
            snapshot = PluginInfoSnapshot.from_payload(payload)
        except Exception:
            return
        async with cls._lock:
            cls._store_snapshot(snapshot)

    @classmethod
    async def remove(
        cls, module: str | None = None, module_path: str | None = None
    ) -> None:
        if not module and not module_path:
            return
        async with cls._lock:
            if module:
                snapshot = cls._by_module.pop(module, None)
                if snapshot and snapshot.module_path:
                    cls._by_module_path.pop(snapshot.module_path, None)
            if module_path:
                snapshot = cls._by_module_path.pop(module_path, None)
                if snapshot and snapshot.module:
                    cls._by_module.pop(snapshot.module, None)
        RuntimeCacheMutation.publish(
            "plugin",
            "delete",
            {"module": module, "module_path": module_path},
        )

    @classmethod
    async def apply_sync_event(cls, action: str, data: dict[str, Any]) -> None:
        if action == "upsert":
            await cls.upsert_from_payload(data)
        elif action == "delete":
            await cls.remove(data.get("module"), data.get("module_path"))
        elif action == "refresh":
            await cls.refresh()

    @classmethod
    async def _refresh_loop(cls, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await cls.refresh()
            except Exception as exc:
                logger.error("plugin cache refresh failed", LOG_COMMAND, e=exc)

    @classmethod
    def start_refresh_task(cls) -> None:
        interval = PLUGININFO_MEM_REFRESH_INTERVAL
        if interval <= 0:
            return
        if cls._refresh_task and not cls._refresh_task.done():
            return
        cls._refresh_task = asyncio.create_task(cls._refresh_loop(interval))

    @classmethod
    def stop_tasks(cls) -> None:
        if cls._refresh_task and not cls._refresh_task.done():
            cls._refresh_task.cancel()
        cls._refresh_task = None


class BotMemoryCache:
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _by_id: ClassVar[dict[str, BotSnapshot]] = {}
    _negative: ClassVar[dict[str, float]] = {}
    _loaded: ClassVar[bool] = False
    _refresh_task: ClassVar[asyncio.Task | None] = None
    _last_refresh: ClassVar[float] = 0.0
    _last_error: ClassVar[str | None] = None

    @classmethod
    def _normalize(cls, bot_id: str | None) -> str | None:
        if bot_id is None:
            return None
        bot_id = bot_id.strip()
        return bot_id if bot_id else None

    @classmethod
    def _negative_ttl(cls) -> int:
        return BOT_MEM_NEGATIVE_TTL

    @classmethod
    def _is_negative(cls, bot_id: str) -> bool:
        expire_at = cls._negative.get(bot_id)
        if not expire_at:
            return False
        if expire_at <= time.time():
            RuntimeCacheMutation.clear_negative_key(cls, bot_id)
            return False
        return True

    @classmethod
    def _mark_negative(cls, bot_id: str) -> None:
        ttl = cls._negative_ttl()
        if ttl <= 0:
            return
        cls._negative[bot_id] = time.time() + ttl

    @classmethod
    async def refresh(cls) -> None:
        from zhenxun.models.bot_console import BotConsole

        async with cls._lock:
            records = await RuntimeCacheMutation.read_db(
                cls,
                BotConsole.all(),
                operation="BotMemoryCache.refresh",
            )
            if records is None:
                return
            cls._by_id = {str(r.bot_id): BotSnapshot.from_model(r) for r in records}
            RuntimeCacheMutation.clear_negative_all(cls)
            RuntimeCacheMutation.mark_refreshed(cls)
            logger.debug(f"bot cache refreshed: {len(cls._by_id)} entries", LOG_COMMAND)

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await RuntimeCacheMutation.ensure_loaded(cls, "bot")

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._loaded

    @classmethod
    async def get(cls, bot_id: str | None) -> BotSnapshot | None:
        bot_id = cls._normalize(bot_id)
        if not bot_id:
            return None
        if not cls._loaded:
            await cls.ensure_loaded()
        entry = cls._by_id.get(bot_id)
        if entry:
            return entry
        if cls._is_negative(bot_id):
            return None
        cls._mark_negative(bot_id)
        return None

    @classmethod
    def get_if_ready(cls, bot_id: str | None) -> BotSnapshot | None:
        bot_id = cls._normalize(bot_id)
        if not bot_id or not cls._loaded:
            return None
        entry = cls._by_id.get(bot_id)
        if entry:
            return entry
        if cls._is_negative(bot_id):
            return None
        cls._mark_negative(bot_id)
        return None

    @classmethod
    async def get_all(cls) -> dict[str, BotSnapshot]:
        if not cls._loaded:
            await cls.ensure_loaded()
        return dict(cls._by_id)

    @classmethod
    async def update_status(cls, bot_id: str | None, status: bool) -> None:
        bot_id = cls._normalize(bot_id)
        if not bot_id:
            return
        async with cls._lock:
            entry = cls._by_id.get(bot_id)
            if not entry:
                return
            updated = BotSnapshot(
                bot_id=entry.bot_id,
                status=bool(status),
                platform=entry.platform,
                block_plugins=entry.block_plugins,
                block_tasks=entry.block_tasks,
                available_plugins=entry.available_plugins,
                available_tasks=entry.available_tasks,
            )
            cls._by_id[bot_id] = updated
            RuntimeCacheMutation.clear_negative_key(cls, bot_id)
        RuntimeCacheMutation.publish("bot", "upsert", updated.to_payload())

    @classmethod
    async def upsert_from_model(cls, record) -> None:
        entry = BotSnapshot.from_model(record)
        async with cls._lock:
            cls._by_id[entry.bot_id] = entry
            RuntimeCacheMutation.clear_negative_key(cls, entry.bot_id)
        RuntimeCacheMutation.publish("bot", "upsert", entry.to_payload())

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        entry = BotSnapshot.from_payload(payload)
        if not entry.bot_id:
            return
        async with cls._lock:
            cls._by_id[entry.bot_id] = entry
            RuntimeCacheMutation.clear_negative_key(cls, entry.bot_id)

    @classmethod
    async def remove(cls, bot_id: str | None) -> None:
        bot_id = cls._normalize(bot_id)
        if not bot_id:
            return
        async with cls._lock:
            cls._by_id.pop(bot_id, None)
            RuntimeCacheMutation.clear_negative_key(cls, bot_id)
        RuntimeCacheMutation.publish("bot", "delete", {"bot_id": bot_id})

    @classmethod
    async def apply_sync_event(cls, action: str, data: dict[str, Any]) -> None:
        if action == "upsert":
            await cls.upsert_from_payload(data)
        elif action == "delete":
            await cls.remove(data.get("bot_id"))
        elif action == "refresh":
            await cls.refresh()

    @classmethod
    async def _refresh_loop(cls, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await cls.refresh()
            except Exception as exc:
                logger.error("bot cache refresh failed", LOG_COMMAND, e=exc)

    @classmethod
    def start_tasks(cls) -> None:
        interval = BOT_MEM_REFRESH_INTERVAL
        if interval <= 0:
            return
        if cls._refresh_task and not cls._refresh_task.done():
            return
        cls._refresh_task = asyncio.create_task(cls._refresh_loop(interval))

    @classmethod
    def stop_tasks(cls) -> None:
        if cls._refresh_task and not cls._refresh_task.done():
            cls._refresh_task.cancel()
        cls._refresh_task = None


class GroupMemoryCache:
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _by_key: ClassVar[dict[tuple[str, str], GroupSnapshot]] = {}
    _negative: ClassVar[dict[tuple[str, str], float]] = {}
    _loaded: ClassVar[bool] = False
    _refresh_task: ClassVar[asyncio.Task | None] = None
    _last_refresh: ClassVar[float] = 0.0
    _last_error: ClassVar[str | None] = None

    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value if value else None

    @classmethod
    def _key(
        cls, group_id: str | None, channel_id: str | None
    ) -> tuple[str, str] | None:
        group_id = cls._normalize(group_id)
        if not group_id:
            return None
        channel_id = cls._normalize(channel_id) or ""
        return (group_id, channel_id)

    @classmethod
    def _negative_ttl(cls) -> int:
        return GROUP_MEM_NEGATIVE_TTL

    @classmethod
    def _is_negative(cls, key: tuple[str, str]) -> bool:
        expire_at = cls._negative.get(key)
        if not expire_at:
            return False
        if expire_at <= time.time():
            RuntimeCacheMutation.clear_negative_key(cls, key)
            return False
        return True

    @classmethod
    def _mark_negative(cls, key: tuple[str, str]) -> None:
        ttl = cls._negative_ttl()
        if ttl <= 0:
            return
        cls._negative[key] = time.time() + ttl

    @classmethod
    async def refresh(cls) -> None:
        from zhenxun.models.group_console import GroupConsole

        async with cls._lock:
            records = await RuntimeCacheMutation.read_db(
                cls,
                GroupConsole.all(),
                operation="GroupMemoryCache.refresh",
            )
            if records is None:
                return
            by_key: dict[tuple[str, str], GroupSnapshot] = {}
            for record in records:
                entry = GroupSnapshot.from_model(record)
                key = cls._key(entry.group_id, entry.channel_id)
                if key:
                    by_key[key] = entry
            cls._by_key = by_key
            RuntimeCacheMutation.clear_negative_all(cls)
            RuntimeCacheMutation.mark_refreshed(cls)
            logger.debug(f"group cache refreshed: {len(by_key)} entries", LOG_COMMAND)

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await RuntimeCacheMutation.ensure_loaded(cls, "group")

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._loaded

    @classmethod
    async def get(
        cls, group_id: str | None, channel_id: str | None = None
    ) -> GroupSnapshot | None:
        key = cls._key(group_id, channel_id)
        if not key:
            return None
        if not cls._loaded:
            await cls.ensure_loaded()
        entry = cls._by_key.get(key)
        if entry:
            return entry
        if cls._is_negative(key):
            return None
        cls._mark_negative(key)
        return None

    @classmethod
    def get_if_ready(
        cls, group_id: str | None, channel_id: str | None = None
    ) -> GroupSnapshot | None:
        key = cls._key(group_id, channel_id)
        if not key:
            return None
        if not cls._loaded:
            return None
        entry = cls._by_key.get(key)
        if entry:
            return entry
        if cls._is_negative(key):
            return None
        cls._mark_negative(key)
        return None

    @classmethod
    async def upsert_from_model(cls, record) -> None:
        entry = GroupSnapshot.from_model(record)
        key = cls._key(entry.group_id, entry.channel_id)
        if not key:
            return
        async with cls._lock:
            cls._by_key[key] = entry
            RuntimeCacheMutation.clear_negative_key(cls, key)
        RuntimeCacheMutation.publish("group", "upsert", entry.to_payload())

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        entry = GroupSnapshot.from_payload(payload)
        key = cls._key(entry.group_id, entry.channel_id)
        if not key:
            return
        async with cls._lock:
            cls._by_key[key] = entry
            RuntimeCacheMutation.clear_negative_key(cls, key)

    @classmethod
    async def remove(cls, group_id: str | None, channel_id: str | None = None) -> None:
        key = cls._key(group_id, channel_id)
        if not key:
            return
        async with cls._lock:
            cls._by_key.pop(key, None)
            RuntimeCacheMutation.clear_negative_key(cls, key)
        RuntimeCacheMutation.publish(
            "group", "delete", {"group_id": key[0], "channel_id": key[1] or None}
        )

    @classmethod
    async def apply_sync_event(cls, action: str, data: dict[str, Any]) -> None:
        if action == "upsert":
            await cls.upsert_from_payload(data)
        elif action == "delete":
            await cls.remove(data.get("group_id"), data.get("channel_id"))
        elif action == "refresh":
            await cls.refresh()

    @classmethod
    async def _refresh_loop(cls, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await cls.refresh()
            except Exception as exc:
                logger.error("group cache refresh failed", LOG_COMMAND, e=exc)

    @classmethod
    def start_tasks(cls) -> None:
        interval = GROUP_MEM_REFRESH_INTERVAL
        if interval <= 0:
            return
        if cls._refresh_task and not cls._refresh_task.done():
            return
        cls._refresh_task = asyncio.create_task(cls._refresh_loop(interval))

    @classmethod
    def stop_tasks(cls) -> None:
        if cls._refresh_task and not cls._refresh_task.done():
            cls._refresh_task.cancel()
        cls._refresh_task = None


class LevelUserMemoryCache:
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _by_key: ClassVar[dict[tuple[str, str], LevelUserSnapshot]] = {}
    _by_user_max: ClassVar[dict[str, int]] = {}
    _negative: ClassVar[dict[tuple[str, str], float]] = {}
    _loaded: ClassVar[bool] = False
    _refresh_task: ClassVar[asyncio.Task | None] = None
    _last_refresh: ClassVar[float] = 0.0

    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value if value else ""

    @classmethod
    def _key(cls, user_id: str | None, group_id: str | None) -> tuple[str, str] | None:
        user_id = cls._normalize(user_id)
        if not user_id:
            return None
        group_id = cls._normalize(group_id) or ""
        return (user_id, group_id)

    @classmethod
    def _negative_ttl(cls) -> int:
        return LEVEL_MEM_NEGATIVE_TTL

    @classmethod
    def _is_negative(cls, key: tuple[str, str]) -> bool:
        expire_at = cls._negative.get(key)
        if not expire_at:
            return False
        if expire_at <= time.time():
            RuntimeCacheMutation.clear_negative_key(cls, key)
            return False
        return True

    @classmethod
    def _mark_negative(cls, key: tuple[str, str]) -> None:
        ttl = cls._negative_ttl()
        if ttl <= 0:
            return
        cls._negative[key] = time.time() + ttl

    @classmethod
    async def refresh(cls) -> None:
        from zhenxun.models.level_user import LevelUser

        async with cls._lock:
            records = await RuntimeCacheMutation.read_db(
                cls,
                LevelUser.all(),
                operation="LevelUserMemoryCache.refresh",
            )
            if records is None:
                return
            by_key: dict[tuple[str, str], LevelUserSnapshot] = {}
            by_user_max: dict[str, int] = {}
            for record in records:
                entry = LevelUserSnapshot.from_model(record)
                key = cls._key(entry.user_id, entry.group_id)
                if key:
                    by_key[key] = entry
                    current = by_user_max.get(entry.user_id, 0)
                    if entry.user_level > current:
                        by_user_max[entry.user_id] = entry.user_level
            cls._by_key = by_key
            cls._by_user_max = by_user_max
            RuntimeCacheMutation.clear_negative_all(cls)
            RuntimeCacheMutation.mark_refreshed(cls)
            logger.debug(f"level cache refreshed: {len(by_key)} entries", LOG_COMMAND)

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await RuntimeCacheMutation.ensure_loaded(cls, "level")

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._loaded

    @classmethod
    async def ensure_fresh(cls) -> None:
        interval = LEVEL_MEM_REFRESH_INTERVAL
        if not cls._loaded:
            await cls.refresh()
            return
        if interval <= 0:
            return
        if time.time() - cls._last_refresh > interval:
            await cls.refresh()

    @classmethod
    async def get(
        cls, user_id: str | None, group_id: str | None
    ) -> LevelUserSnapshot | None:
        key = cls._key(user_id, group_id)
        if not key:
            return None
        if not cls._loaded:
            await cls.ensure_loaded()
        entry = cls._by_key.get(key)
        if entry:
            return entry
        if cls._is_negative(key):
            return None
        cls._mark_negative(key)
        return None

    @classmethod
    async def get_levels(
        cls, user_id: str | None, group_id: str | None
    ) -> tuple[LevelUserSnapshot | None, LevelUserSnapshot | None]:
        if not cls._loaded:
            await cls.ensure_loaded()
        global_user = None
        group_user = None
        global_key = cls._key(user_id, "")
        if global_key:
            global_user = cls._by_key.get(global_key)
        if group_id:
            group_key = cls._key(user_id, group_id)
            if group_key:
                group_user = cls._by_key.get(group_key)
        return global_user, group_user

    @classmethod
    def get_levels_if_ready(
        cls, user_id: str | None, group_id: str | None
    ) -> tuple[LevelUserSnapshot | None, LevelUserSnapshot | None] | None:
        if not cls._loaded:
            return None
        global_user = None
        group_user = None
        global_key = cls._key(user_id, "")
        if global_key:
            global_user = cls._by_key.get(global_key)
        if group_id:
            group_key = cls._key(user_id, group_id)
            if group_key:
                group_user = cls._by_key.get(group_key)
        return global_user, group_user

    @classmethod
    async def get_max_level(cls, user_id: str | None) -> int:
        user_id = cls._normalize(user_id)
        if not user_id:
            return 0
        if not cls._loaded:
            await cls.ensure_loaded()
        return cls._by_user_max.get(user_id, 0)

    @classmethod
    async def upsert_from_model(cls, record) -> None:
        entry = LevelUserSnapshot.from_model(record)
        key = cls._key(entry.user_id, entry.group_id)
        if not key:
            return
        async with cls._lock:
            prev = cls._by_key.get(key)
            cls._by_key[key] = entry
            current = cls._by_user_max.get(entry.user_id, 0)
            if entry.user_level >= current:
                cls._by_user_max[entry.user_id] = entry.user_level
            elif prev and prev.user_level == current and entry.user_level < current:
                cls._recalc_user_max(entry.user_id)
            RuntimeCacheMutation.clear_negative_key(cls, key)
        RuntimeCacheMutation.publish("level", "upsert", entry.to_payload())

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        entry = LevelUserSnapshot.from_payload(payload)
        key = cls._key(entry.user_id, entry.group_id)
        if not key:
            return
        async with cls._lock:
            prev = cls._by_key.get(key)
            cls._by_key[key] = entry
            current = cls._by_user_max.get(entry.user_id, 0)
            if entry.user_level >= current:
                cls._by_user_max[entry.user_id] = entry.user_level
            elif prev and prev.user_level == current and entry.user_level < current:
                cls._recalc_user_max(entry.user_id)
            RuntimeCacheMutation.clear_negative_key(cls, key)

    @classmethod
    async def remove(cls, user_id: str | None, group_id: str | None) -> None:
        key = cls._key(user_id, group_id)
        if not key:
            return
        async with cls._lock:
            removed = cls._by_key.pop(key, None)
            if removed and cls._by_user_max.get(removed.user_id) == removed.user_level:
                cls._recalc_user_max(removed.user_id)
            RuntimeCacheMutation.clear_negative_key(cls, key)
        RuntimeCacheMutation.publish(
            "level", "delete", {"user_id": key[0], "group_id": key[1] or None}
        )

    @classmethod
    def _recalc_user_max(cls, user_id: str) -> None:
        max_level = 0
        for entry in cls._by_key.values():
            if entry.user_id == user_id and entry.user_level > max_level:
                max_level = entry.user_level
        if max_level:
            cls._by_user_max[user_id] = max_level
        else:
            cls._by_user_max.pop(user_id, None)

    @classmethod
    async def apply_sync_event(cls, action: str, data: dict[str, Any]) -> None:
        if action == "upsert":
            await cls.upsert_from_payload(data)
        elif action == "delete":
            await cls.remove(data.get("user_id"), data.get("group_id"))
        elif action == "refresh":
            await cls.refresh()

    @classmethod
    async def _refresh_loop(cls, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await cls.refresh()
            except Exception as exc:
                logger.error("level cache refresh failed", LOG_COMMAND, e=exc)

    @classmethod
    def start_tasks(cls) -> None:
        interval = LEVEL_MEM_REFRESH_INTERVAL
        if interval <= 0:
            return
        if cls._refresh_task and not cls._refresh_task.done():
            return
        cls._refresh_task = asyncio.create_task(cls._refresh_loop(interval))

    @classmethod
    def stop_tasks(cls) -> None:
        if cls._refresh_task and not cls._refresh_task.done():
            cls._refresh_task.cancel()
        cls._refresh_task = None


class TaskInfoMemoryCache:
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _by_module: ClassVar[dict[str, TaskInfoSnapshot]] = {}
    _by_name: ClassVar[dict[str, TaskInfoSnapshot]] = {}
    _negative: ClassVar[dict[str, float]] = {}
    _loaded: ClassVar[bool] = False
    _refresh_task: ClassVar[asyncio.Task | None] = None
    _last_refresh: ClassVar[float] = 0.0
    _last_error: ClassVar[str | None] = None

    @classmethod
    def _normalize(cls, module: str | None) -> str | None:
        if module is None:
            return None
        module = module.strip()
        return module if module else None

    @classmethod
    def _negative_ttl(cls) -> int:
        return TASK_MEM_NEGATIVE_TTL

    @classmethod
    def _is_negative(cls, module: str) -> bool:
        expire_at = cls._negative.get(module)
        if not expire_at:
            return False
        if expire_at <= time.time():
            RuntimeCacheMutation.clear_negative_key(cls, module)
            return False
        return True

    @classmethod
    def _mark_negative(cls, module: str) -> None:
        ttl = cls._negative_ttl()
        if ttl <= 0:
            return
        cls._negative[module] = time.time() + ttl

    @classmethod
    async def refresh(cls) -> None:
        from zhenxun.models.task_info import TaskInfo

        async with cls._lock:
            records = await RuntimeCacheMutation.read_db(
                cls,
                TaskInfo.all(),
                operation="TaskInfoMemoryCache.refresh",
            )
            if records is None:
                return
            by_module: dict[str, TaskInfoSnapshot] = {}
            by_name: dict[str, TaskInfoSnapshot] = {}
            for record in records:
                entry = TaskInfoSnapshot.from_model(record)
                by_module[entry.module] = entry
                if entry.name:
                    by_name[entry.name] = entry
            cls._by_module = by_module
            cls._by_name = by_name
            RuntimeCacheMutation.clear_negative_all(cls)
            RuntimeCacheMutation.mark_refreshed(cls)
            logger.debug(
                f"task info cache refreshed: {len(cls._by_module)} entries",
                LOG_COMMAND,
            )

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await RuntimeCacheMutation.ensure_loaded(cls, "task")

    @classmethod
    async def get(cls, module: str | None) -> TaskInfoSnapshot | None:
        module = cls._normalize(module)
        if not module:
            return None
        if not cls._loaded:
            await cls.ensure_loaded()
        entry = cls._by_module.get(module)
        if entry:
            return entry
        if cls._is_negative(module):
            return None
        cls._mark_negative(module)
        return None

    @classmethod
    async def get_by_name(cls, name: str | None) -> TaskInfoSnapshot | None:
        name = (name or "").strip()
        if not name:
            return None
        if not cls._loaded:
            await cls.ensure_loaded()
        return cls._by_name.get(name)

    @classmethod
    async def get_all(cls) -> list[TaskInfoSnapshot]:
        if not cls._loaded:
            await cls.ensure_loaded()
        return sorted(cls._by_module.values(), key=lambda item: (item.id, item.module))

    @classmethod
    async def is_disabled(cls, module: str | None) -> bool:
        """Backward-compatible runtime disabled check for passive tasks."""
        return await cls.is_runtime_disabled(module)

    @classmethod
    async def is_runtime_disabled(cls, module: str | None) -> bool:
        """Return whether a passive task is unavailable at runtime.

        Runtime passive availability is defined by TaskInfo.status and
        TaskInfo.load_status. Bot/group scoped block lists are checked by
        CommonUtils.task_is_block().
        """
        entry = await cls.get(module)
        if not entry:
            return False
        return not entry.status or not entry.load_status

    @classmethod
    async def upsert_from_model(cls, record) -> None:
        entry = TaskInfoSnapshot.from_model(record)
        async with cls._lock:
            cls._by_module[entry.module] = entry
            if entry.name:
                cls._by_name[entry.name] = entry
            RuntimeCacheMutation.clear_negative_key(cls, entry.module)
        RuntimeCacheMutation.publish("task", "upsert", entry.to_payload())

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        entry = TaskInfoSnapshot.from_payload(payload)
        if not entry.module:
            return
        async with cls._lock:
            cls._by_module[entry.module] = entry
            if entry.name:
                cls._by_name[entry.name] = entry
            RuntimeCacheMutation.clear_negative_key(cls, entry.module)

    @classmethod
    async def remove(cls, module: str | None) -> None:
        module = cls._normalize(module)
        if not module:
            return
        async with cls._lock:
            removed = cls._by_module.pop(module, None)
            if removed and removed.name:
                current = cls._by_name.get(removed.name)
                if current and current.module == removed.module:
                    cls._by_name.pop(removed.name, None)
            RuntimeCacheMutation.clear_negative_key(cls, module)
        RuntimeCacheMutation.publish("task", "delete", {"module": module})

    @classmethod
    async def apply_sync_event(cls, action: str, data: dict[str, Any]) -> None:
        if action == "upsert":
            await cls.upsert_from_payload(data)
        elif action == "delete":
            await cls.remove(data.get("module"))
        elif action == "refresh":
            await cls.refresh()

    @classmethod
    async def _refresh_loop(cls, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await cls.refresh()
            except Exception as exc:
                logger.error("task info cache refresh failed", LOG_COMMAND, e=exc)

    @classmethod
    def start_tasks(cls) -> None:
        interval = TASK_MEM_REFRESH_INTERVAL
        if interval <= 0:
            return
        if cls._refresh_task and not cls._refresh_task.done():
            return
        cls._refresh_task = asyncio.create_task(cls._refresh_loop(interval))

    @classmethod
    def stop_tasks(cls) -> None:
        if cls._refresh_task and not cls._refresh_task.done():
            cls._refresh_task.cancel()
        cls._refresh_task = None


class PluginLimitMemoryCache:
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _by_id: ClassVar[dict[int, PluginLimitSnapshot]] = {}
    _by_module: ClassVar[dict[str, list[PluginLimitSnapshot]]] = {}
    _negative: ClassVar[dict[str, float]] = {}
    _loaded: ClassVar[bool] = False
    _refresh_task: ClassVar[asyncio.Task | None] = None
    _last_refresh: ClassVar[float] = 0.0
    _last_error: ClassVar[str | None] = None

    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value if value else None

    @classmethod
    def _negative_ttl(cls) -> int:
        return LIMIT_MEM_NEGATIVE_TTL

    @classmethod
    def _is_negative(cls, module: str) -> bool:
        expire_at = cls._negative.get(module)
        if not expire_at:
            return False
        if expire_at <= time.time():
            RuntimeCacheMutation.clear_negative_key(cls, module)
            return False
        return True

    @classmethod
    def _mark_negative(cls, module: str) -> None:
        ttl = cls._negative_ttl()
        if ttl <= 0:
            return
        cls._negative[module] = time.time() + ttl

    @classmethod
    async def refresh(cls) -> None:
        from zhenxun.models.plugin_limit import PluginLimit

        async with cls._lock:
            records = await RuntimeCacheMutation.read_db(
                cls,
                PluginLimit.filter(status=True).all(),
                operation="PluginLimitMemoryCache.refresh",
            )
            if records is None:
                return
            by_id: dict[int, PluginLimitSnapshot] = {}
            by_module: dict[str, list[PluginLimitSnapshot]] = {}
            for record in records:
                entry = PluginLimitSnapshot.from_model(record)
                by_id[entry.id] = entry
                by_module.setdefault(entry.module, []).append(entry)
            cls._by_id = by_id
            cls._by_module = by_module
            RuntimeCacheMutation.clear_negative_all(cls)
            RuntimeCacheMutation.mark_refreshed(cls)
            logger.debug(
                f"plugin limit cache refreshed: {len(by_id)} entries",
                LOG_COMMAND,
            )

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await RuntimeCacheMutation.ensure_loaded(cls, "plugin_limit")

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._loaded

    @classmethod
    async def get_limits(cls, module: str) -> list[PluginLimitSnapshot]:
        normalized = cls._normalize(module)
        if not normalized:
            return []
        module = normalized
        if not cls._loaded:
            await cls.ensure_loaded()
        limits = cls._by_module.get(module)
        if limits is not None:
            return limits
        if cls._is_negative(module):
            return []
        cls._mark_negative(module)
        return []

    @classmethod
    def get_limits_if_ready(cls, module: str) -> list[PluginLimitSnapshot] | None:
        normalized = cls._normalize(module)
        if not normalized:
            return []
        module = normalized
        if not cls._loaded:
            return None
        limits = cls._by_module.get(module)
        if limits is not None:
            return limits
        if cls._is_negative(module):
            return []
        cls._mark_negative(module)
        return []

    @classmethod
    def get_all_limits(cls) -> list[PluginLimitSnapshot]:
        return list(cls._by_id.values())

    @classmethod
    async def upsert_from_model(cls, record) -> None:
        entry = PluginLimitSnapshot.from_model(record)
        await cls._upsert_entry(entry)
        RuntimeCacheMutation.publish("plugin_limit", "upsert", entry.to_payload())

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        try:
            entry = PluginLimitSnapshot.from_payload(payload)
        except Exception:
            return
        await cls._upsert_entry(entry)

    @classmethod
    async def _upsert_entry(cls, entry: PluginLimitSnapshot) -> None:
        async with cls._lock:
            prev = cls._by_id.get(entry.id)
            if prev and prev.module != entry.module:
                cls._by_module[prev.module] = [
                    item
                    for item in cls._by_module.get(prev.module, [])
                    if item.id != prev.id
                ]
            if not entry.status:
                cls._by_id.pop(entry.id, None)
                cls._by_module[entry.module] = [
                    item
                    for item in cls._by_module.get(entry.module, [])
                    if item.id != entry.id
                ]
                RuntimeCacheMutation.clear_negative_key(cls, entry.module)
                return
            cls._by_id[entry.id] = entry
            module_limits = [
                item
                for item in cls._by_module.get(entry.module, [])
                if item.id != entry.id
            ]
            module_limits.append(entry)
            cls._by_module[entry.module] = module_limits
            RuntimeCacheMutation.clear_negative_key(cls, entry.module)

    @classmethod
    async def remove_by_id(cls, limit_id: int | None) -> None:
        if not limit_id:
            return
        async with cls._lock:
            entry = cls._by_id.pop(limit_id, None)
            if entry:
                cls._by_module[entry.module] = [
                    item
                    for item in cls._by_module.get(entry.module, [])
                    if item.id != entry.id
                ]
                RuntimeCacheMutation.clear_negative_key(cls, entry.module)
        RuntimeCacheMutation.publish("plugin_limit", "delete", {"id": int(limit_id)})

    @classmethod
    async def apply_sync_event(cls, action: str, data: dict[str, Any]) -> None:
        if action == "upsert":
            await cls.upsert_from_payload(data)
        elif action == "delete":
            await cls.remove_by_id(data.get("id"))
        elif action == "refresh":
            await cls.refresh()

    @classmethod
    async def _refresh_loop(cls, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await cls.refresh()
            except Exception as exc:
                logger.error("plugin limit cache refresh failed", LOG_COMMAND, e=exc)

    @classmethod
    def start_tasks(cls) -> None:
        interval = LIMIT_MEM_REFRESH_INTERVAL
        if interval <= 0:
            return
        if cls._refresh_task and not cls._refresh_task.done():
            return
        cls._refresh_task = asyncio.create_task(cls._refresh_loop(interval))

    @classmethod
    def stop_tasks(cls) -> None:
        if cls._refresh_task and not cls._refresh_task.done():
            cls._refresh_task.cancel()
        cls._refresh_task = None


class BanMemoryCache:
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _by_user: ClassVar[dict[str, BanEntry]] = {}
    _by_group: ClassVar[dict[str, BanEntry]] = {}
    _by_user_group: ClassVar[dict[tuple[str, str], BanEntry]] = {}
    _negative: ClassVar[dict[tuple[str | None, str | None], float]] = {}
    _loaded: ClassVar[bool] = False
    _refresh_task: ClassVar[asyncio.Task | None] = None
    _cleanup_task: ClassVar[asyncio.Task | None] = None
    _remove_tasks: ClassVar[set[asyncio.Task]] = set()
    _last_refresh: ClassVar[float] = 0.0
    _last_error: ClassVar[str | None] = None

    @classmethod
    def _normalize_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value if value else None

    @classmethod
    def _neg_ttl(cls) -> int:
        return BAN_MEM_NEGATIVE_TTL

    @classmethod
    def _neg_key(
        cls, user_id: str | None, group_id: str | None
    ) -> tuple[str | None, str | None]:
        return (cls._normalize_id(user_id), cls._normalize_id(group_id))

    @classmethod
    def _is_negative(cls, key: tuple[str | None, str | None]) -> bool:
        expire_at = cls._negative.get(key)
        if not expire_at:
            return False
        if expire_at <= time.time():
            RuntimeCacheMutation.clear_negative_key(cls, key)
            return False
        return True

    @classmethod
    def _mark_negative(cls, key: tuple[str | None, str | None]) -> None:
        ttl = cls._neg_ttl()
        if ttl <= 0:
            return
        cls._negative[key] = time.time() + ttl

    @classmethod
    def _build_entry(cls, record) -> BanEntry | None:
        user_id = cls._normalize_id(record.user_id)
        group_id = cls._normalize_id(record.group_id)
        duration = int(record.duration)
        if duration == -1:
            expire_at = None
        else:
            expire_at = float(record.ban_time + duration)
        return BanEntry(
            user_id=user_id,
            group_id=group_id,
            ban_level=int(record.ban_level),
            ban_time=int(record.ban_time),
            duration=duration,
            expire_at=expire_at,
        )

    @classmethod
    async def refresh(cls) -> None:
        from zhenxun.models.ban_console import BanConsole

        async with cls._lock:
            now_ts = time.time()
            records = await RuntimeCacheMutation.read_db(
                cls,
                BanConsole.all(),
                operation="BanMemoryCache.refresh",
            )
            if records is None:
                return
            by_user: dict[str, BanEntry] = {}
            by_group: dict[str, BanEntry] = {}
            by_user_group: dict[tuple[str, str], BanEntry] = {}
            for record in records:
                entry = cls._build_entry(record)
                if not entry:
                    continue
                if entry.expire_at is not None and entry.expire_at <= now_ts:
                    continue
                if entry.user_id and entry.group_id:
                    by_user_group[(entry.user_id, entry.group_id)] = entry
                elif entry.user_id:
                    by_user[entry.user_id] = entry
                elif entry.group_id:
                    by_group[entry.group_id] = entry
            cls._by_user = by_user
            cls._by_group = by_group
            cls._by_user_group = by_user_group
            RuntimeCacheMutation.clear_negative_all(cls)
            RuntimeCacheMutation.mark_refreshed(cls)
            logger.debug(
                "ban cache refreshed: "
                f"user={len(by_user)} group={len(by_group)} "
                f"user_group={len(by_user_group)}",
                LOG_COMMAND,
            )

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await RuntimeCacheMutation.ensure_loaded(cls, "ban")

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._loaded

    @classmethod
    async def upsert_from_model(cls, record) -> None:
        entry = cls._build_entry(record)
        if not entry:
            return
        async with cls._lock:
            if entry.user_id and entry.group_id:
                cls._by_user_group[(entry.user_id, entry.group_id)] = entry
            elif entry.user_id:
                cls._by_user[entry.user_id] = entry
            elif entry.group_id:
                cls._by_group[entry.group_id] = entry
            RuntimeCacheMutation.clear_negative_all(cls)
        RuntimeCacheMutation.publish("ban", "upsert", entry.to_payload())

    @classmethod
    async def remove(cls, user_id: str | None, group_id: str | None) -> None:
        await cls._remove_local(user_id, group_id)
        RuntimeCacheMutation.publish(
            "ban", "delete", {"user_id": user_id, "group_id": group_id}
        )

    @classmethod
    async def _remove_local(cls, user_id: str | None, group_id: str | None) -> None:
        user_id = cls._normalize_id(user_id)
        group_id = cls._normalize_id(group_id)
        async with cls._lock:
            if user_id and group_id:
                cls._by_user_group.pop((user_id, group_id), None)
            elif user_id:
                cls._by_user.pop(user_id, None)
            elif group_id:
                cls._by_group.pop(group_id, None)
            RuntimeCacheMutation.clear_negative_all(cls)

    @classmethod
    def _get_entry(cls, user_id: str | None, group_id: str | None) -> BanEntry | None:
        user_id = cls._normalize_id(user_id)
        group_id = cls._normalize_id(group_id)
        if user_id and group_id:
            entry = cls._by_user_group.get((user_id, group_id))
            if entry:
                return entry
            entry = cls._by_user.get(user_id)
            if entry:
                return entry
            return None
        if user_id:
            return cls._by_user.get(user_id)
        if group_id:
            return cls._by_group.get(group_id)
        return None

    @classmethod
    def is_banned(cls, user_id: str | None, group_id: str | None) -> bool:
        if not cls._loaded:
            return False
        neg_key = cls._neg_key(user_id, group_id)
        if cls._is_negative(neg_key):
            return False
        entry = cls._get_entry(user_id, group_id)
        if not entry:
            cls._mark_negative(neg_key)
            return False
        remaining = entry.remaining()
        if remaining == 0 and entry.duration != -1:
            task = asyncio.create_task(cls.remove(entry.user_id, entry.group_id))
            cls._remove_tasks.add(task)
            task.add_done_callback(cls._remove_tasks.discard)
            return False
        return True

    @classmethod
    def remaining_time(cls, user_id: str | None, group_id: str | None) -> int:
        if not cls._loaded:
            return 0
        neg_key = cls._neg_key(user_id, group_id)
        if cls._is_negative(neg_key):
            return 0
        entry = cls._get_entry(user_id, group_id)
        if not entry:
            cls._mark_negative(neg_key)
            return 0
        remaining = entry.remaining()
        if remaining == 0 and entry.duration != -1:
            task = asyncio.create_task(cls.remove(entry.user_id, entry.group_id))
            cls._remove_tasks.add(task)
            task.add_done_callback(cls._remove_tasks.discard)
            return 0
        return remaining

    @classmethod
    def check_ban_level(
        cls, user_id: str | None, group_id: str | None, level: int
    ) -> bool:
        if not cls._loaded:
            return False
        neg_key = cls._neg_key(user_id, group_id)
        if cls._is_negative(neg_key):
            return False
        entry = cls._get_entry(user_id, group_id)
        if not entry:
            cls._mark_negative(neg_key)
            return False
        remaining = entry.remaining()
        if remaining == 0 and entry.duration != -1:
            task = asyncio.create_task(cls.remove(entry.user_id, entry.group_id))
            cls._remove_tasks.add(task)
            task.add_done_callback(cls._remove_tasks.discard)
            return False
        return entry.ban_level <= level

    @classmethod
    async def cleanup_expired(cls, delete_db: bool = True) -> None:
        now_ts = time.time()
        expired: list[BanEntry] = []
        async with cls._lock:
            for entry in list(cls._by_user.values()):
                if entry.expire_at is not None and entry.expire_at <= now_ts:
                    expired.append(entry)
            for entry in list(cls._by_group.values()):
                if entry.expire_at is not None and entry.expire_at <= now_ts:
                    expired.append(entry)
            for entry in list(cls._by_user_group.values()):
                if entry.expire_at is not None and entry.expire_at <= now_ts:
                    expired.append(entry)
            for entry in expired:
                if entry.user_id and entry.group_id:
                    cls._by_user_group.pop((entry.user_id, entry.group_id), None)
                elif entry.user_id:
                    cls._by_user.pop(entry.user_id, None)
                elif entry.group_id:
                    cls._by_group.pop(entry.group_id, None)
            if expired:
                RuntimeCacheMutation.clear_negative_all(cls)
        if not delete_db or not expired:
            return
        from tortoise.expressions import Q

        from zhenxun.models.ban_console import BanConsole

        for entry in expired:
            query = BanConsole.filter()
            if entry.user_id:
                query = query.filter(user_id=entry.user_id)
            else:
                query = query.filter(Q(user_id__isnull=True) | Q(user_id=""))
            if entry.group_id:
                query = query.filter(group_id=entry.group_id)
            else:
                query = query.filter(Q(group_id__isnull=True) | Q(group_id=""))
            await query.delete()

    @classmethod
    async def _refresh_loop(cls, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await cls.refresh()
            except Exception as exc:
                logger.error("ban cache refresh failed", LOG_COMMAND, e=exc)

    @classmethod
    async def _cleanup_loop(cls, interval: int, delete_db: bool) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await cls.cleanup_expired(delete_db=delete_db)
            except Exception as exc:
                logger.error("ban cache cleanup failed", LOG_COMMAND, e=exc)

    @classmethod
    def start_tasks(cls) -> None:
        refresh_interval = BAN_MEM_REFRESH_INTERVAL
        clean_interval = BAN_MEM_CLEAN_INTERVAL
        cleanup_db = BAN_MEM_CLEANUP_DB

        if refresh_interval > 0 and (not cls._refresh_task or cls._refresh_task.done()):
            cls._refresh_task = asyncio.create_task(cls._refresh_loop(refresh_interval))
        if clean_interval > 0 and (not cls._cleanup_task or cls._cleanup_task.done()):
            cls._cleanup_task = asyncio.create_task(
                cls._cleanup_loop(clean_interval, cleanup_db)
            )

    @classmethod
    def stop_tasks(cls) -> None:
        if cls._refresh_task and not cls._refresh_task.done():
            cls._refresh_task.cancel()
        if cls._cleanup_task and not cls._cleanup_task.done():
            cls._cleanup_task.cancel()
        cls._refresh_task = None
        cls._cleanup_task = None

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        entry = BanEntry.from_payload(payload)
        async with cls._lock:
            if entry.user_id and entry.group_id:
                cls._by_user_group[(entry.user_id, entry.group_id)] = entry
            elif entry.user_id:
                cls._by_user[entry.user_id] = entry
            elif entry.group_id:
                cls._by_group[entry.group_id] = entry
            RuntimeCacheMutation.clear_negative_all(cls)

    @classmethod
    async def apply_sync_event(cls, action: str, data: dict[str, Any]) -> None:
        if action == "upsert":
            await cls.upsert_from_payload(data)
        elif action == "delete":
            await cls._remove_local(data.get("user_id"), data.get("group_id"))
        elif action == "refresh":
            await cls.refresh()


async def _safe_refresh(cache_cls: type, label: str) -> None:
    """安全地刷新单个缓存，异常不影响其他缓存。"""
    if getattr(cache_cls, "_loaded", False):
        last_refresh = float(getattr(cache_cls, "_last_refresh", 0.0) or 0.0)
        if time.time() - last_refresh <= RUNTIME_CACHE_STARTUP_REFRESH_SKIP_SECONDS:
            logger.debug(f"{label} cache startup refresh skipped", LOG_COMMAND)
            return
    try:
        await cache_cls.refresh()
    except Exception as exc:
        RuntimeCacheMutation.mark_error(cache_cls, exc)
        logger.error(f"{label} cache init failed", LOG_COMMAND, e=exc)


def _cache_health(
    cache_cls: type,
    *,
    entry_count: int,
    negative_count: int = 0,
) -> dict[str, Any]:
    return {
        "loaded": bool(getattr(cache_cls, "_loaded", False)),
        "entry_count": entry_count,
        "last_refresh": float(getattr(cache_cls, "_last_refresh", 0.0) or 0.0),
        "negative_count": negative_count,
        "last_error": getattr(cache_cls, "_last_error", None),
    }


def health_snapshot() -> dict[str, dict[str, Any]]:
    """Return in-memory runtime cache health without touching the database."""
    return {
        "plugin": _cache_health(
            PluginInfoMemoryCache,
            entry_count=len(PluginInfoMemoryCache._by_module),
        ),
        "bot": _cache_health(
            BotMemoryCache,
            entry_count=len(BotMemoryCache._by_id),
            negative_count=len(BotMemoryCache._negative),
        ),
        "group": _cache_health(
            GroupMemoryCache,
            entry_count=len(GroupMemoryCache._by_key),
            negative_count=len(GroupMemoryCache._negative),
        ),
        "level": _cache_health(
            LevelUserMemoryCache,
            entry_count=len(LevelUserMemoryCache._by_key),
            negative_count=len(LevelUserMemoryCache._negative),
        ),
        "task": _cache_health(
            TaskInfoMemoryCache,
            entry_count=len(TaskInfoMemoryCache._by_module),
            negative_count=len(TaskInfoMemoryCache._negative),
        ),
        "plugin_limit": _cache_health(
            PluginLimitMemoryCache,
            entry_count=len(PluginLimitMemoryCache._by_id),
            negative_count=len(PluginLimitMemoryCache._negative),
        ),
        "ban": _cache_health(
            BanMemoryCache,
            entry_count=(
                len(BanMemoryCache._by_user)
                + len(BanMemoryCache._by_group)
                + len(BanMemoryCache._by_user_group)
            ),
            negative_count=len(BanMemoryCache._negative),
        ),
    }


def passive_status_snapshot(max_modules: int = 50) -> dict[str, Any]:
    """Return passive-task state from in-memory caches only.

    This is a local diagnostic helper: it does not query or write the database,
    and it is not used by runtime decisions.
    """
    tasks = list(TaskInfoMemoryCache._by_module.values())
    disabled = sorted(task.module for task in tasks if not task.status)
    unloaded = sorted(task.module for task in tasks if not task.load_status)
    runtime_enabled = [
        task.module for task in tasks if task.status and task.load_status
    ]
    bot_block_total = sum(
        len(_parse_block_modules(bot.block_tasks))
        for bot in BotMemoryCache._by_id.values()
    )
    group_block_total = sum(
        len(group.block_task_set) + len(group.superuser_block_task_set)
        for group in GroupMemoryCache._by_key.values()
    )
    return {
        "cache": health_snapshot(),
        "passive_tasks": {
            "total": len(tasks),
            "status_enabled": sum(1 for task in tasks if task.status),
            "load_status_enabled": sum(1 for task in tasks if task.load_status),
            "runtime_enabled": len(runtime_enabled),
            "disabled_modules": disabled[:max_modules],
            "disabled_modules_total": len(disabled),
            "unloaded_modules": unloaded[:max_modules],
            "unloaded_modules_total": len(unloaded),
        },
        "scoped_blocks": {
            "bot_block_tasks_total": bot_block_total,
            "group_block_tasks_total": group_block_total,
        },
        "semantics": {
            "available_tasks": "management_display_mirror_not_runtime_whitelist",
            "runtime_truth": [
                "TaskInfo.status",
                "TaskInfo.load_status",
                "BotConsole.block_tasks",
                "GroupConsole.block_task",
                "GroupConsole.superuser_block_task",
            ],
        },
    }


@PriorityLifecycle.on_startup(priority=6)
async def _init_runtime_cache():
    await RuntimeCacheSync.start()
    # 并发刷新所有缓存，互不依赖
    await asyncio.gather(
        _safe_refresh(PluginInfoMemoryCache, "plugin"),
        _safe_refresh(BotMemoryCache, "bot"),
        _safe_refresh(GroupMemoryCache, "group"),
        _safe_refresh(LevelUserMemoryCache, "level"),
        _safe_refresh(TaskInfoMemoryCache, "task info"),
        _safe_refresh(PluginLimitMemoryCache, "plugin limit"),
        _safe_refresh(BanMemoryCache, "ban"),
    )
    PluginInfoMemoryCache.start_refresh_task()
    BotMemoryCache.start_tasks()
    GroupMemoryCache.start_tasks()
    LevelUserMemoryCache.start_tasks()
    TaskInfoMemoryCache.start_tasks()
    PluginLimitMemoryCache.start_tasks()
    BanMemoryCache.start_tasks()
    _CACHE_READY_EVENT.set()


@PriorityLifecycle.on_shutdown(priority=6)
async def _stop_runtime_cache():
    PluginInfoMemoryCache.stop_tasks()
    BotMemoryCache.stop_tasks()
    GroupMemoryCache.stop_tasks()
    LevelUserMemoryCache.stop_tasks()
    TaskInfoMemoryCache.stop_tasks()
    PluginLimitMemoryCache.stop_tasks()
    BanMemoryCache.stop_tasks()
    await RuntimeCacheSync.stop()
