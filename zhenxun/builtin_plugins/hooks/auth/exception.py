class IsSuperuserException(Exception):
    pass


class SkipPluginException(Exception):
    def __init__(self, info: str, *args: object) -> None:
        super().__init__(*args)
        self.info = info

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
