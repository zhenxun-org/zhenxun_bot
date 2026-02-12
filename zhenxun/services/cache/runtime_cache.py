from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
import time
from typing import TYPE_CHECKING, Any, ClassVar
import uuid

from zhenxun.services.cache.config import CacheMode
from zhenxun.services.log import logger
from zhenxun.utils.enum import LimitCheckType, LimitWatchType, PluginLimitType
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

if TYPE_CHECKING:
    from zhenxun.models.plugin_info import PluginInfo

LOG_COMMAND = "RuntimeCache"

PLUGININFO_MEM_REFRESH_INTERVAL = 300
BAN_MEM_REFRESH_INTERVAL = 60
BAN_MEM_CLEAN_INTERVAL = 60
BAN_MEM_CLEANUP_DB = True
BAN_MEM_NEGATIVE_TTL = 5
BOT_MEM_REFRESH_INTERVAL = 60
BOT_MEM_NEGATIVE_TTL = 60
GROUP_MEM_REFRESH_INTERVAL = 60
GROUP_MEM_NEGATIVE_TTL = 60
LEVEL_MEM_REFRESH_INTERVAL = 120
LEVEL_MEM_NEGATIVE_TTL = 60
TASK_MEM_REFRESH_INTERVAL = 900
TASK_MEM_NEGATIVE_TTL = 60
LIMIT_MEM_REFRESH_INTERVAL = 60
LIMIT_MEM_NEGATIVE_TTL = 30
RUNTIME_CACHE_SYNC_ENABLED = True
RUNTIME_CACHE_SYNC_CHANNEL = "ZHENXUN_RUNTIME_CACHE_SYNC"


def _coerce_int(value, default: int) -> int:
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        return default
    return value_int if value_int >= 0 else default


INSTANCE_ID = uuid.uuid4().hex
_CACHE_READY_EVENT = asyncio.Event()


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
    module: str
    status: bool
    load_status: bool
    default_status: bool

    @classmethod
    def from_model(cls, model) -> "TaskInfoSnapshot":
        return cls(
            module=str(model.module),
            status=bool(getattr(model, "status", True)),
            load_status=bool(getattr(model, "load_status", True)),
            default_status=bool(getattr(model, "default_status", True)),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "status": self.status,
            "load_status": self.load_status,
            "default_status": self.default_status,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TaskInfoSnapshot":
        return cls(
            module=str(payload.get("module", "")),
            status=bool(payload.get("status", True)),
            load_status=bool(payload.get("load_status", True)),
            default_status=bool(payload.get("default_status", True)),
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
        cls._ready = False

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


class PluginInfoMemoryCache:
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _by_module: ClassVar[dict[str, "PluginInfo"]] = {}
    _by_module_path: ClassVar[dict[str, "PluginInfo"]] = {}
    _loaded: ClassVar[bool] = False
    _refresh_task: ClassVar[asyncio.Task | None] = None
    _last_refresh: ClassVar[float] = 0.0

    @classmethod
    async def refresh(cls) -> None:
        from zhenxun.models.plugin_info import PluginInfo

        async with cls._lock:
            plugins = await PluginInfo.all()
            by_module: dict[str, "PluginInfo"] = {}
            by_module_path: dict[str, "PluginInfo"] = {}
            for plugin in plugins:
                if plugin.module:
                    by_module[plugin.module] = plugin
                if plugin.module_path:
                    by_module_path[plugin.module_path] = plugin
            cls._by_module = by_module
            cls._by_module_path = by_module_path
            cls._loaded = True
            cls._last_refresh = time.time()
            logger.debug(
                f"plugin cache refreshed: {len(by_module)} entries", LOG_COMMAND
            )

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await cls.refresh()

    @classmethod
    async def get_by_module(cls, module: str) -> "PluginInfo | None":
        if not cls._loaded:
            await cls.ensure_loaded()
        return cls._by_module.get(module)

    @classmethod
    def get_by_module_path(cls, module_path: str) -> "PluginInfo | None":
        return cls._by_module_path.get(module_path)

    @classmethod
    def set_plugin(cls, plugin) -> None:
        if not plugin:
            return
        if plugin.module:
            cls._by_module[plugin.module] = plugin
        if getattr(plugin, "module_path", None):
            cls._by_module_path[plugin.module_path] = plugin
        cls._loaded = True
        cls._last_refresh = time.time()

    @classmethod
    def remove_by_module(cls, module: str) -> None:
        cls._by_module.pop(module, None)

    @classmethod
    async def upsert_from_model(cls, plugin) -> None:
        if not plugin:
            return
        async with cls._lock:
            if getattr(plugin, "module", None):
                cls._by_module[plugin.module] = plugin
            if getattr(plugin, "module_path", None):
                cls._by_module_path[plugin.module_path] = plugin
            cls._loaded = True
            cls._last_refresh = time.time()

    @classmethod
    async def remove(
        cls, module: str | None = None, module_path: str | None = None
    ) -> None:
        if not module and not module_path:
            return
        async with cls._lock:
            if module:
                cls._by_module.pop(module, None)
            if module_path:
                cls._by_module_path.pop(module_path, None)

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
            cls._negative.pop(bot_id, None)
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
            records = await BotConsole.all()
            cls._by_id = {str(r.bot_id): BotSnapshot.from_model(r) for r in records}
            cls._negative = {}
            cls._loaded = True
            logger.debug(f"bot cache refreshed: {len(cls._by_id)} entries", LOG_COMMAND)

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await cls.refresh()

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
        RuntimeCacheSync.publish_event("bot", "upsert", updated.to_payload())

    @classmethod
    async def upsert_from_model(cls, record) -> None:
        entry = BotSnapshot.from_model(record)
        async with cls._lock:
            cls._by_id[entry.bot_id] = entry
            cls._negative.pop(entry.bot_id, None)
        RuntimeCacheSync.publish_event("bot", "upsert", entry.to_payload())

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        entry = BotSnapshot.from_payload(payload)
        if not entry.bot_id:
            return
        async with cls._lock:
            cls._by_id[entry.bot_id] = entry
            cls._negative.pop(entry.bot_id, None)

    @classmethod
    async def remove(cls, bot_id: str | None) -> None:
        bot_id = cls._normalize(bot_id)
        if not bot_id:
            return
        async with cls._lock:
            cls._by_id.pop(bot_id, None)
        RuntimeCacheSync.publish_event("bot", "delete", {"bot_id": bot_id})

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
            cls._negative.pop(key, None)
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
            records = await GroupConsole.all()
            by_key: dict[tuple[str, str], GroupSnapshot] = {}
            for record in records:
                entry = GroupSnapshot.from_model(record)
                key = cls._key(entry.group_id, entry.channel_id)
                if key:
                    by_key[key] = entry
            cls._by_key = by_key
            cls._negative = {}
            cls._loaded = True
            logger.debug(f"group cache refreshed: {len(by_key)} entries", LOG_COMMAND)

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await cls.refresh()

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
            cls._negative.pop(key, None)
        RuntimeCacheSync.publish_event("group", "upsert", entry.to_payload())

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        entry = GroupSnapshot.from_payload(payload)
        key = cls._key(entry.group_id, entry.channel_id)
        if not key:
            return
        async with cls._lock:
            cls._by_key[key] = entry
            cls._negative.pop(key, None)

    @classmethod
    async def remove(cls, group_id: str | None, channel_id: str | None = None) -> None:
        key = cls._key(group_id, channel_id)
        if not key:
            return
        async with cls._lock:
            cls._by_key.pop(key, None)
        RuntimeCacheSync.publish_event(
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
            cls._negative.pop(key, None)
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
            records = await LevelUser.all()
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
            cls._negative = {}
            cls._loaded = True
            cls._last_refresh = time.time()
            logger.debug(f"level cache refreshed: {len(by_key)} entries", LOG_COMMAND)

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await cls.refresh()

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
            cls._negative.pop(key, None)
            current = cls._by_user_max.get(entry.user_id, 0)
            if entry.user_level >= current:
                cls._by_user_max[entry.user_id] = entry.user_level
            elif prev and prev.user_level == current and entry.user_level < current:
                cls._recalc_user_max(entry.user_id)
        RuntimeCacheSync.publish_event("level", "upsert", entry.to_payload())

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        entry = LevelUserSnapshot.from_payload(payload)
        key = cls._key(entry.user_id, entry.group_id)
        if not key:
            return
        async with cls._lock:
            prev = cls._by_key.get(key)
            cls._by_key[key] = entry
            cls._negative.pop(key, None)
            current = cls._by_user_max.get(entry.user_id, 0)
            if entry.user_level >= current:
                cls._by_user_max[entry.user_id] = entry.user_level
            elif prev and prev.user_level == current and entry.user_level < current:
                cls._recalc_user_max(entry.user_id)

    @classmethod
    async def remove(cls, user_id: str | None, group_id: str | None) -> None:
        key = cls._key(user_id, group_id)
        if not key:
            return
        async with cls._lock:
            removed = cls._by_key.pop(key, None)
            if removed and cls._by_user_max.get(removed.user_id) == removed.user_level:
                cls._recalc_user_max(removed.user_id)
        RuntimeCacheSync.publish_event(
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
    _negative: ClassVar[dict[str, float]] = {}
    _loaded: ClassVar[bool] = False
    _refresh_task: ClassVar[asyncio.Task | None] = None

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
            cls._negative.pop(module, None)
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
            records = await TaskInfo.all()
            cls._by_module = {r.module: TaskInfoSnapshot.from_model(r) for r in records}
            cls._negative = {}
            cls._loaded = True
            logger.debug(
                f"task info cache refreshed: {len(cls._by_module)} entries",
                LOG_COMMAND,
            )

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await cls.refresh()

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
    async def is_disabled(cls, module: str | None) -> bool:
        entry = await cls.get(module)
        if not entry:
            return False
        return not entry.status

    @classmethod
    async def upsert_from_model(cls, record) -> None:
        entry = TaskInfoSnapshot.from_model(record)
        async with cls._lock:
            cls._by_module[entry.module] = entry
            cls._negative.pop(entry.module, None)
        RuntimeCacheSync.publish_event("task", "upsert", entry.to_payload())

    @classmethod
    async def upsert_from_payload(cls, payload: dict[str, Any]) -> None:
        entry = TaskInfoSnapshot.from_payload(payload)
        if not entry.module:
            return
        async with cls._lock:
            cls._by_module[entry.module] = entry
            cls._negative.pop(entry.module, None)

    @classmethod
    async def remove(cls, module: str | None) -> None:
        module = cls._normalize(module)
        if not module:
            return
        async with cls._lock:
            cls._by_module.pop(module, None)
        RuntimeCacheSync.publish_event("task", "delete", {"module": module})

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
            cls._negative.pop(module, None)
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
            records = await PluginLimit.filter(status=True).all()
            by_id: dict[int, PluginLimitSnapshot] = {}
            by_module: dict[str, list[PluginLimitSnapshot]] = {}
            for record in records:
                entry = PluginLimitSnapshot.from_model(record)
                by_id[entry.id] = entry
                by_module.setdefault(entry.module, []).append(entry)
            cls._by_id = by_id
            cls._by_module = by_module
            cls._negative = {}
            cls._loaded = True
            logger.debug(
                f"plugin limit cache refreshed: {len(by_id)} entries",
                LOG_COMMAND,
            )

    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        await cls.refresh()

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
    def get_all_limits(cls) -> list[PluginLimitSnapshot]:
        return list(cls._by_id.values())

    @classmethod
    async def upsert_from_model(cls, record) -> None:
        entry = PluginLimitSnapshot.from_model(record)
        await cls._upsert_entry(entry)
        RuntimeCacheSync.publish_event("plugin_limit", "upsert", entry.to_payload())

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
                return
            cls._by_id[entry.id] = entry
            module_limits = [
                item
                for item in cls._by_module.get(entry.module, [])
                if item.id != entry.id
            ]
            module_limits.append(entry)
            cls._by_module[entry.module] = module_limits
            cls._negative.pop(entry.module, None)

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
        RuntimeCacheSync.publish_event("plugin_limit", "delete", {"id": int(limit_id)})

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
            cls._negative.pop(key, None)
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
            records = await BanConsole.all()
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
            cls._negative = {}
            cls._loaded = True
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
        await cls.refresh()

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
            cls._negative = {}
        RuntimeCacheSync.publish_event("ban", "upsert", entry.to_payload())

    @classmethod
    async def remove(cls, user_id: str | None, group_id: str | None) -> None:
        await cls._remove_local(user_id, group_id)
        RuntimeCacheSync.publish_event(
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
            cls._negative = {}

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
                cls._negative = {}
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
            cls._negative = {}

    @classmethod
    async def apply_sync_event(cls, action: str, data: dict[str, Any]) -> None:
        if action == "upsert":
            await cls.upsert_from_payload(data)
        elif action == "delete":
            await cls._remove_local(data.get("user_id"), data.get("group_id"))
        elif action == "refresh":
            await cls.refresh()


@PriorityLifecycle.on_startup(priority=6)
async def _init_runtime_cache():
    await RuntimeCacheSync.start()
    try:
        await PluginInfoMemoryCache.refresh()
    except Exception as exc:
        logger.error("plugin cache init failed", LOG_COMMAND, e=exc)
    try:
        await BotMemoryCache.refresh()
    except Exception as exc:
        logger.error("bot cache init failed", LOG_COMMAND, e=exc)
    try:
        await GroupMemoryCache.refresh()
    except Exception as exc:
        logger.error("group cache init failed", LOG_COMMAND, e=exc)
    try:
        await LevelUserMemoryCache.refresh()
    except Exception as exc:
        logger.error("level cache init failed", LOG_COMMAND, e=exc)
    try:
        await TaskInfoMemoryCache.refresh()
    except Exception as exc:
        logger.error("task info cache init failed", LOG_COMMAND, e=exc)
    try:
        await PluginLimitMemoryCache.refresh()
    except Exception as exc:
        logger.error("plugin limit cache init failed", LOG_COMMAND, e=exc)
    try:
        await BanMemoryCache.refresh()
    except Exception as exc:
        logger.error("ban cache init failed", LOG_COMMAND, e=exc)
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
