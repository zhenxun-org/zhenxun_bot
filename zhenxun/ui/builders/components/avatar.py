from typing import Literal
from typing_extensions import Self

from ...models.components.avatar import Avatar, AvatarGroup
from ..base import BaseBuilder


class AvatarBuilder(BaseBuilder[Avatar]):
    """链式构建单个头像的辅助类"""

    def __init__(self, src: str):
        data_model = Avatar(src=src, shape="circle", size=50)
        super().__init__(data_model, template_name="components/widgets/avatar")

    def set_shape(self, shape: Literal["circle", "square"]) -> Self:
        self._data.shape = shape
        return self

    def set_size(self, size: int) -> Self:
        self._data.size = size
        return self


class AvatarGroupBuilder(BaseBuilder[AvatarGroup]):
    """链式构建头像组的辅助类"""

    def __init__(self):
        data_model = AvatarGroup(avatars=[], spacing=-15, max_count=None)
        super().__init__(data_model, template_name="components/widgets/avatar_group")

    def add_avatar(self, avatar: Avatar | AvatarBuilder | str) -> Self:
        if isinstance(avatar, str):
            self._data.avatars.append(Avatar(src=avatar, shape="circle", size=50))
        elif isinstance(avatar, AvatarBuilder):
            self._data.avatars.append(avatar.build())
        else:
            self._data.avatars.append(avatar)
        return self
