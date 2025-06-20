from typing import Any, Literal

from nonebot.compat import model_dump
from pydantic import BaseModel

from zhenxun.utils.enum import PluginType

type2name: dict[str, str] = {
    "NORMAL": "普通插件",
    "ADMIN": "管理员插件",
    "SUPERUSER": "超级用户插件",
    "ADMIN_SUPERUSER": "管理员/超级用户插件",
    "DEPENDANT": "依赖插件",
    "HIDDEN": "其他插件",
}


class GiteeContents(BaseModel):
    """Gitee Api内容"""

    type: Literal["file", "dir"]
    """类型"""
    size: Any
    """文件大小"""
    name: str
    """文件名"""
    path: str
    """文件路径"""
    url: str
    """文件链接"""
    html_url: str
    """文件html链接"""
    download_url: str
    """文件raw链接"""


class StorePluginInfo(BaseModel):
    """插件信息"""

    name: str
    """插件名"""
    module: str
    """模块名"""
    module_path: str
    """模块路径"""
    description: str
    """简介"""
    usage: str
    """用法"""
    author: str
    """作者"""
    version: str
    """版本"""
    plugin_type: PluginType
    """插件类型"""
    is_dir: bool
    """是否为文件夹插件"""
    github_url: str | None = None
    """github链接"""

    @property
    def plugin_type_name(self):
        return type2name[self.plugin_type.value]

    def to_dict(self, **kwargs):
        return model_dump(self, **kwargs)
