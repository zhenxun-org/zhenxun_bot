class ForceAddGroupError(Exception):
    """
    强制拉群
    """

    def __init__(self, info: str, group_id: str):
        super().__init__(self)
        self._info = info
        self._group_id = group_id

    def get_info(self) -> str:
        return self._info

    def get_group_id(self) -> str:
        return self._group_id
