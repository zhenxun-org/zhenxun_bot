class HookPriorityException(BaseException):
    """
    钩子优先级异常
    """

    def __init__(self, info: str = "") -> None:
        self.info = info

    def __str__(self) -> str:
        return self.info


class NotFoundError(Exception):
    """
    未发现
    """

    pass


class GroupInfoNotFound(Exception):
    """
    群组未找到
    """

    pass


class EmptyError(Exception):
    """
    空错误
    """

    pass


class UserAndGroupIsNone(Exception):
    """
    用户和群组为空
    """

    pass


class InsufficientGold(Exception):
    """
    金币不足
    """

    pass


class NotFindSuperuser(Exception):
    """
    未找到超级用户
    """

    pass


class GoodsNotFound(Exception):
    """
    或找到道具
    """

    pass
