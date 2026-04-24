from collections import OrderedDict
import time
from typing import ClassVar


class MessageManager:
    _MAX_USERS: ClassVar[int] = 4096
    _MAX_MESSAGES_PER_USER: ClassVar[int] = 200
    _TRIM_MESSAGES_TO: ClassVar[int] = 100
    _USER_TTL_SECONDS: ClassVar[float] = 6 * 60 * 60
    data: ClassVar[OrderedDict[str, tuple[float, list[str]]]] = OrderedDict()

    @classmethod
    def _prune(cls, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        stale_before = now - cls._USER_TTL_SECONDS
        stale_uids = [
            uid for uid, (last_seen, _) in cls.data.items() if last_seen <= stale_before
        ]
        for uid in stale_uids:
            cls.data.pop(uid, None)
        while len(cls.data) > cls._MAX_USERS:
            cls.data.popitem(last=False)

    @classmethod
    def _touch(cls, uid: str, messages: list[str], now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        cls.data[uid] = (now, messages)
        cls.data.move_to_end(uid)

    @classmethod
    def add(cls, uid: str, msg_id: str):
        now = time.monotonic()
        cls._prune(now)
        _, messages = cls.data.get(uid, (now, []))
        messages.append(msg_id)
        cls._touch(uid, messages, now)
        cls.remove_check(uid)
        cls._prune(now)

    @classmethod
    def check(cls, uid: str, msg_id: str) -> bool:
        now = time.monotonic()
        cls._prune(now)
        entry = cls.data.get(uid)
        if entry is None:
            return False
        _, messages = entry
        cls._touch(uid, messages, now)
        return msg_id in messages

    @classmethod
    def remove_check(cls, uid: str):
        entry = cls.data.get(uid)
        if entry is None:
            return
        _, messages = entry
        if len(messages) > cls._MAX_MESSAGES_PER_USER:
            messages = messages[-cls._TRIM_MESSAGES_TO :]
            cls._touch(uid, messages)

    @classmethod
    def get(cls, uid: str) -> list[str]:
        now = time.monotonic()
        cls._prune(now)
        entry = cls.data.get(uid)
        if entry is None:
            return []
        _, messages = entry
        cls._touch(uid, messages, now)
        return list(messages)

    @classmethod
    def clear_all(cls) -> int:
        size = len(cls.data)
        cls.data.clear()
        return size
