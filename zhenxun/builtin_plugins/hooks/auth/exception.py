class IsSuperuserException(Exception):
    pass


class SkipPluginException(Exception):
    def __init__(
        self,
        info: str,
        *args: object,
        tip_message: list | str | None = None,
        tip_check_tag: str | None = None,
        tip_background: bool = False,
        tip_timeout: float | None = None,
    ) -> None:
        super().__init__(*args)
        self.info = info
        self.tip_message = tip_message
        self.tip_check_tag = tip_check_tag
        self.tip_background = tip_background
        self.tip_timeout = tip_timeout

    def __str__(self) -> str:
        return self.info

    def __repr__(self) -> str:
        return self.info


class PermissionExemption(Exception):
    def __init__(self, info: str, *args: object) -> None:
        super().__init__(*args)
        self.info = info

    def __str__(self) -> str:
        return self.info

    def __repr__(self) -> str:
        return self.info
