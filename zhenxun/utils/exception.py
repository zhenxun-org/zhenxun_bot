from nonebot.exception import IgnoredException


class CooldownError(IgnoredException):
    """
    冷却异常，用于在冷却时中断事件处理。
    继承自 IgnoredException，不会在控制台留下错误堆栈。
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


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


class AllURIsFailedError(Exception):
    """
    当所有备用URL都尝试失败后抛出此异常
    """

    def __init__(self, urls: list[str], exceptions: list[Exception]):
        self.urls = urls
        self.exceptions = exceptions
        super().__init__(
            f"All {len(urls)} URIs failed. Last exception: {exceptions[-1]}"
        )

    def __str__(self) -> str:
        exc_info = "\n".join(
            f"  - {url}: {exc.__class__.__name__}({exc})"
            for url, exc in zip(self.urls, self.exceptions)
        )
        return f"All {len(self.urls)} URIs failed:\n{exc_info}"
