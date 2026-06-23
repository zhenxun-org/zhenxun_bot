from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from tortoise.functions import Count

from zhenxun.services.cache.bounded_ttl import BoundedTTLCache
from zhenxun.services.db_context import with_db_timeout
from zhenxun.services.message_load import is_db_unhealthy


@dataclass(frozen=True, slots=True)
class GroupMemberSnapshot:
    id: int
    user_id: str
    user_name: str
    group_id: str
    user_join_time: datetime | None
    uid: int | None
    platform: str | None


def _member_cache_sizeof(members: tuple[GroupMemberSnapshot, ...]) -> int:
    size = 0
    for member in members:
        size += 96
        size += len(member.user_id) + len(member.user_name) + len(member.group_id)
        size += len(member.platform or "")
    return size


_GROUP_MEMBER_CACHE = BoundedTTLCache[str, tuple[GroupMemberSnapshot, ...]](
    "hot_group_info_users",
    ttl_seconds=45,
    max_items=512,
    max_total_bytes=32 * 1024 * 1024,
    sizeof=_member_cache_sizeof,
)
_GROUP_USER_IDS_CACHE = BoundedTTLCache[str, tuple[str, ...]](
    "hot_group_info_user_ids",
    ttl_seconds=45,
    max_items=2048,
)
_GROUP_MEMBER_BY_ID_CACHE = BoundedTTLCache[str, tuple[GroupMemberSnapshot | None]](
    "hot_group_info_user_by_id",
    ttl_seconds=45,
    max_items=50000,
)
_USER_GROUP_CACHE = BoundedTTLCache[str, tuple[str, ...]](
    "hot_group_info_user_groups",
    ttl_seconds=45,
    max_items=4096,
)
_USER_NAME_CACHE = BoundedTTLCache[str, str](
    "hot_group_info_user_names",
    ttl_seconds=45,
    max_items=20000,
)
_CHAT_RANK_CACHE = BoundedTTLCache[str, tuple[tuple[str, int], ...]](
    "hot_chat_history_rank",
    ttl_seconds=20,
    max_items=512,
)
_CHAT_FIRST_MSG_CACHE = BoundedTTLCache[str, tuple[datetime | None]](
    "hot_chat_history_first_msg",
    ttl_seconds=300,
    max_items=2048,
)
_STATISTICS_COUNT_CACHE = BoundedTTLCache[str, tuple[tuple[str, int], ...]](
    "hot_statistics_plugin_counts",
    ttl_seconds=20,
    max_items=512,
)

_GROUP_MEMBER_LOCKS: dict[str, asyncio.Lock] = {}
_GROUP_USER_IDS_LOCKS: dict[str, asyncio.Lock] = {}
_USER_GROUP_LOCKS: dict[str, asyncio.Lock] = {}
_CHAT_RANK_LOCKS: dict[str, asyncio.Lock] = {}
_CHAT_FIRST_MSG_LOCKS: dict[str, asyncio.Lock] = {}
_STATISTICS_LOCKS: dict[str, asyncio.Lock] = {}
_MAX_LOCK_POOL_SIZE = 4096
_MEMBER_DB_TIMEOUT = 2.0
_AGGREGATE_DB_TIMEOUT = 3.0


async def _read_or_default(
    coro,
    *,
    timeout: float,
    operation: str,
    default,
):
    try:
        return await with_db_timeout(
            coro,
            timeout=timeout,
            operation=operation,
            source="hot_query_cache",
        )
    except TimeoutError:
        return default


def _get_lock(pool: dict[str, asyncio.Lock], key: str) -> asyncio.Lock:
    lock = pool.get(key)
    if lock is None:
        if len(pool) >= _MAX_LOCK_POOL_SIZE:
            for old_key, old_lock in list(pool.items()):
                if not old_lock.locked():
                    pool.pop(old_key, None)
                    break
        lock = asyncio.Lock()
        pool[key] = lock
    return lock


def _normalize_id(value: object) -> str:
    return str(value or "")


def _normalize_ids(values: Iterable[object] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    return tuple(dict.fromkeys(v for value in values if (v := _normalize_id(value))))


async def get_group_members(
    group_id: str | int | None,
) -> tuple[GroupMemberSnapshot, ...]:
    """Return lightweight group-member snapshots with a short runtime TTL."""
    group_key = _normalize_id(group_id)
    if not group_key:
        return ()

    cached = await _GROUP_MEMBER_CACHE.get(group_key)
    if cached is not None:
        return cached

    lock = _get_lock(_GROUP_MEMBER_LOCKS, group_key)
    async with lock:
        cached = await _GROUP_MEMBER_CACHE.get(group_key)
        if cached is not None:
            return cached

        from zhenxun.models.group_member_info import GroupInfoUser

        if is_db_unhealthy():
            return ()
        rows = await _read_or_default(
            GroupInfoUser.filter(group_id=group_key).values_list(
                "id",
                "user_id",
                "user_name",
                "user_join_time",
                "uid",
                "platform",
            ),
            timeout=_MEMBER_DB_TIMEOUT,
            operation="hot_query_cache.get_group_members",
            default=(),
        )
        members = tuple(
            GroupMemberSnapshot(
                id=int(row[0] or 0),
                user_id=str(row[1] or ""),
                user_name=str(row[2] or ""),
                group_id=group_key,
                user_join_time=row[3],
                uid=int(row[4]) if row[4] is not None else None,
                platform=str(row[5]) if row[5] else None,
            )
            for row in rows
            if row[1]
        )
        await _GROUP_MEMBER_CACHE.set(group_key, members)
        await _GROUP_USER_IDS_CACHE.set(
            group_key, tuple(member.user_id for member in members)
        )
        return members


async def get_group_member_map(
    group_id: str | int | None,
    user_ids: Iterable[object] | None = None,
) -> dict[str, GroupMemberSnapshot]:
    group_key = _normalize_id(group_id)
    if not group_key:
        return {}
    wanted = _normalize_ids(user_ids)
    if wanted is not None and not wanted:
        return {}
    if wanted is None:
        members = await get_group_members(group_key)
        return {member.user_id: member for member in members}

    cached_members = await _GROUP_MEMBER_CACHE.get(group_key)
    if cached_members is not None:
        wanted_set = set(wanted)
        return {
            member.user_id: member
            for member in cached_members
            if member.user_id in wanted_set
        }

    result: dict[str, GroupMemberSnapshot] = {}
    missing: list[str] = []
    for user_id in wanted:
        cache_key = f"{group_key}:{user_id}"
        cached = await _GROUP_MEMBER_BY_ID_CACHE.get(cache_key)
        if cached is None:
            missing.append(user_id)
        else:
            member = cached[0]
            if member is not None:
                result[user_id] = member

    if missing:
        from zhenxun.models.group_member_info import GroupInfoUser

        if is_db_unhealthy():
            return result
        rows = await _read_or_default(
            GroupInfoUser.filter(group_id=group_key, user_id__in=missing).values_list(
                "id",
                "user_id",
                "user_name",
                "user_join_time",
                "uid",
                "platform",
            ),
            timeout=_MEMBER_DB_TIMEOUT,
            operation="hot_query_cache.get_group_member_map",
            default=(),
        )
        found: set[str] = set()
        for row in rows:
            if not row[1]:
                continue
            member = GroupMemberSnapshot(
                id=int(row[0] or 0),
                user_id=str(row[1] or ""),
                user_name=str(row[2] or ""),
                group_id=group_key,
                user_join_time=row[3],
                uid=int(row[4]) if row[4] is not None else None,
                platform=str(row[5]) if row[5] else None,
            )
            result[member.user_id] = member
            found.add(member.user_id)
            await _GROUP_MEMBER_BY_ID_CACHE.set(
                f"{group_key}:{member.user_id}", (member,)
            )
        for user_id in missing:
            if user_id not in found:
                await _GROUP_MEMBER_BY_ID_CACHE.set(f"{group_key}:{user_id}", (None,))
    return result


async def get_group_member(
    group_id: str | int | None,
    user_id: str | int | None,
) -> GroupMemberSnapshot | None:
    user_key = _normalize_id(user_id)
    if not user_key:
        return None
    return (await get_group_member_map(group_id, [user_key])).get(user_key)


async def get_group_user_ids(group_id: str | int | None) -> set[str]:
    group_key = _normalize_id(group_id)
    if not group_key:
        return set()
    cached = await _GROUP_USER_IDS_CACHE.get(group_key)
    if cached is not None:
        return set(cached)

    cached_members = await _GROUP_MEMBER_CACHE.get(group_key)
    if cached_members is not None:
        user_ids = tuple(member.user_id for member in cached_members)
        await _GROUP_USER_IDS_CACHE.set(group_key, user_ids)
        return set(user_ids)

    lock = _get_lock(_GROUP_USER_IDS_LOCKS, group_key)
    async with lock:
        cached = await _GROUP_USER_IDS_CACHE.get(group_key)
        if cached is not None:
            return set(cached)

        from zhenxun.models.group_member_info import GroupInfoUser

        if is_db_unhealthy():
            return set()
        rows = await _read_or_default(
            GroupInfoUser.filter(group_id=group_key).values_list("user_id", flat=True),
            timeout=_MEMBER_DB_TIMEOUT,
            operation="hot_query_cache.get_group_user_ids",
            default=(),
        )
        user_ids = tuple(str(user_id) for user_id in rows if user_id)
        await _GROUP_USER_IDS_CACHE.set(group_key, user_ids)
        return set(user_ids)


async def get_user_group_ids(user_id: str | int | None) -> list[str]:
    user_key = _normalize_id(user_id)
    if not user_key:
        return []

    cached = await _USER_GROUP_CACHE.get(user_key)
    if cached is not None:
        return list(cached)

    lock = _get_lock(_USER_GROUP_LOCKS, user_key)
    async with lock:
        cached = await _USER_GROUP_CACHE.get(user_key)
        if cached is not None:
            return list(cached)

        from zhenxun.models.group_member_info import GroupInfoUser

        if is_db_unhealthy():
            return []
        rows = await _read_or_default(
            GroupInfoUser.filter(user_id=user_key).values_list("group_id", flat=True),
            timeout=_MEMBER_DB_TIMEOUT,
            operation="hot_query_cache.get_user_group_ids",
            default=(),
        )
        group_ids = tuple(str(group_id) for group_id in rows if group_id)
        await _USER_GROUP_CACHE.set(user_key, group_ids)
        return list(group_ids)


async def get_member_names(
    user_ids: Iterable[object],
    group_id: str | int | None = None,
) -> dict[str, str]:
    user_keys = _normalize_ids(user_ids) or ()
    if not user_keys:
        return {}
    if group_id:
        members = await get_group_member_map(group_id, user_keys)
        return {user_id: members[user_id].user_name for user_id in members}

    result: dict[str, str] = {}
    missing: list[str] = []
    for user_id in user_keys:
        cached = await _USER_NAME_CACHE.get(user_id)
        if cached is None:
            missing.append(user_id)
        else:
            result[user_id] = cached

    if missing:
        from zhenxun.models.group_member_info import GroupInfoUser

        if is_db_unhealthy():
            return result
        rows = await _read_or_default(
            GroupInfoUser.filter(user_id__in=missing).values_list(
                "user_id", "user_name"
            ),
            timeout=_MEMBER_DB_TIMEOUT,
            operation="hot_query_cache.get_member_names",
            default=(),
        )
        for user_id, user_name in rows:
            user_key = str(user_id)
            if user_key not in result:
                result[user_key] = str(user_name or "")
        for user_id in missing:
            await _USER_NAME_CACHE.set(user_id, result.get(user_id, ""))
    return result


async def get_member_name(
    user_id: str | int | None,
    group_id: str | int | None = None,
) -> str | None:
    user_key = _normalize_id(user_id)
    if not user_key:
        return None
    return (await get_member_names([user_key], group_id)).get(user_key) or None


async def invalidate_group_members(
    group_id: str | int | None = None,
    user_ids: Iterable[object] | None = None,
) -> None:
    if group_id is None:
        await _GROUP_MEMBER_CACHE.clear()
        await _GROUP_USER_IDS_CACHE.clear()
        await _GROUP_MEMBER_BY_ID_CACHE.clear()
        return
    group_key = _normalize_id(group_id)
    await _GROUP_MEMBER_CACHE.delete(group_key)
    await _GROUP_USER_IDS_CACHE.delete(group_key)
    normalized_ids = _normalize_ids(user_ids)
    if normalized_ids is None:
        await _GROUP_MEMBER_BY_ID_CACHE.clear()
        return
    for user_id in normalized_ids:
        await _GROUP_MEMBER_BY_ID_CACHE.delete(f"{group_key}:{user_id}")


async def invalidate_member_names(user_ids: Iterable[object] | None = None) -> None:
    if user_ids is None:
        await _USER_NAME_CACHE.clear()
        await _USER_GROUP_CACHE.clear()
        return
    for user_id in _normalize_ids(user_ids) or ():
        await _USER_NAME_CACHE.delete(user_id)
        await _USER_GROUP_CACHE.delete(user_id)


def _datetime_key(value: datetime | None) -> str:
    return value.isoformat(" ", timespec="seconds") if value else ""


def _date_scope_key(date_scope: tuple[datetime, datetime] | None) -> str:
    if not date_scope:
        return ""
    end_bucket = int(date_scope[1].timestamp() // 20)
    return f"{_datetime_key(date_scope[0])}..bucket:{end_bucket}"


async def get_chat_history_rank_cached(
    model: Any,
    gid: str | None,
    limit: int = 10,
    order: str = "DESC",
    date_scope: tuple[datetime, datetime] | None = None,
) -> list[tuple[str, int]]:
    key = f"{gid or '*'}:{limit}:{order}:{_date_scope_key(date_scope)}"
    cached = await _CHAT_RANK_CACHE.get(key)
    if cached is not None:
        return list(cached)

    lock = _get_lock(_CHAT_RANK_LOCKS, key)
    async with lock:
        cached = await _CHAT_RANK_CACHE.get(key)
        if cached is not None:
            return list(cached)

        if is_db_unhealthy():
            return []
        order_prefix = "-" if order == "DESC" else ""
        query: Any = model.filter(group_id=gid) if gid else model
        if date_scope:
            filter_scope = (
                date_scope[0].isoformat(" "),
                date_scope[1].isoformat(" "),
            )
            query = query.filter(create_time__range=filter_scope)
        rows = await _read_or_default(
            query.annotate(count=Count("user_id"))
            .order_by(f"{order_prefix}count")
            .group_by("user_id")
            .limit(limit)
            .values_list("user_id", "count"),
            timeout=_AGGREGATE_DB_TIMEOUT,
            operation="hot_query_cache.get_chat_history_rank",
            default=(),
        )
        result = tuple((str(user_id), int(count)) for user_id, count in rows)
        await _CHAT_RANK_CACHE.set(key, result)
        return list(result)


async def get_chat_history_first_msg_datetime_cached(
    model: Any,
    group_id: str | None,
) -> datetime | None:
    key = group_id or "*"
    cached = await _CHAT_FIRST_MSG_CACHE.get(key)
    if cached is not None:
        return cached[0]

    lock = _get_lock(_CHAT_FIRST_MSG_LOCKS, key)
    async with lock:
        cached = await _CHAT_FIRST_MSG_CACHE.get(key)
        if cached is not None:
            return cached[0]

        if is_db_unhealthy():
            return None
        query: Any = model.filter(group_id=group_id) if group_id else model.all()
        message = await _read_or_default(
            query.order_by("create_time").first(),
            timeout=_AGGREGATE_DB_TIMEOUT,
            operation="hot_query_cache.get_chat_history_first_msg",
            default=None,
        )
        result = getattr(message, "create_time", None) if message else None
        await _CHAT_FIRST_MSG_CACHE.set(key, (result,))
        return result


async def get_statistics_plugin_counts_cached(
    scope: Literal["global", "user", "group"],
    *,
    plugin_name: str | None,
    start_time: datetime | None,
    user_id: str | None = None,
    group_id: str | None = None,
) -> list[tuple[str, int]]:
    key = (
        f"{scope}:{plugin_name or ''}:{_datetime_key(start_time)}:"
        f"{user_id or ''}:{group_id or ''}"
    )
    cached = await _STATISTICS_COUNT_CACHE.get(key)
    if cached is not None:
        return list(cached)

    lock = _get_lock(_STATISTICS_LOCKS, key)
    async with lock:
        cached = await _STATISTICS_COUNT_CACHE.get(key)
        if cached is not None:
            return list(cached)

        if is_db_unhealthy():
            return []
        from zhenxun.models.statistics import Statistics

        query: Any = Statistics
        if scope == "user":
            query = Statistics.filter(user_id=user_id)
            if group_id:
                query = query.filter(group_id=group_id)
        elif scope == "group":
            query = Statistics.filter(group_id=group_id)
        if plugin_name:
            query = query.filter(plugin_name=plugin_name)
        if start_time:
            query = query.filter(create_time__gte=start_time)
        rows = await _read_or_default(
            query.annotate(count=Count("id"))
            .group_by("plugin_name")
            .values_list("plugin_name", "count"),
            timeout=_AGGREGATE_DB_TIMEOUT,
            operation="hot_query_cache.get_statistics_plugin_counts",
            default=(),
        )
        result = tuple((str(plugin), int(count)) for plugin, count in rows)
        await _STATISTICS_COUNT_CACHE.set(key, result)
        return list(result)
