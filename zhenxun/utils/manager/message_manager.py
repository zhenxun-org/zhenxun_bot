from typing import ClassVar


class MessageManager:
    data: ClassVar[dict[str, list[str]]] = {}

    @classmethod
    def add(cls, uid: str, msg_id: str):
        if uid not in cls.data:
            cls.data[uid] = []
        cls.data[uid].append(msg_id)
        cls.remove_check(uid)

    @classmethod
    def check(cls, uid: str, msg_id: str) -> bool:
        return msg_id in cls.data.get(uid, [])

    @classmethod
    def remove_check(cls, uid: str):
        if len(cls.data[uid]) > 200:
            cls.data[uid] = cls.data[uid][100:]

    @classmethod
    def get(cls, uid: str) -> list[str]:
        return cls.data[uid] if uid in cls.data else []
