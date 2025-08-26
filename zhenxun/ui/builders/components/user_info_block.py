from ...models.components.user_info_block import UserInfoBlock
from ..base import BaseBuilder


class UserInfoBlockBuilder(BaseBuilder[UserInfoBlock]):
    """链式构建用户信息块的辅助类"""

    def __init__(
        self,
        name: str,
        avatar_url: str,
        subtitle: str | None = None,
        tags: list[str] | None = None,
    ):
        data_model = UserInfoBlock(
            name=name, avatar_url=avatar_url, subtitle=subtitle, tags=tags or []
        )
        super().__init__(data_model, template_name="components/widgets/user_info_block")

    def set_subtitle(self, subtitle: str) -> "UserInfoBlockBuilder":
        """设置副标题。"""
        self._data.subtitle = subtitle
        return self

    def add_tag(self, tag: str) -> "UserInfoBlockBuilder":
        """添加一个标签。"""
        self._data.tags.append(tag)
        return self

    def add_tags(self, tags: list[str]) -> "UserInfoBlockBuilder":
        """批量添加标签。"""
        self._data.tags.extend(tags)
        return self
