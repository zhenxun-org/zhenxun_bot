from typing import Any

from pydantic import BaseModel, Field

from zhenxun.utils.enum import BlockType


class PluginSwitch(BaseModel):
    """
    插件开关
    """

    module: str
    """模块"""
    status: bool
    """开关状态"""


class UpdateConfig(BaseModel):
    """
    配置项修改参数
    """

    module: str
    """模块"""
    key: str
    """配置项key"""
    value: Any
    """配置项值"""


class UpdatePlugin(BaseModel):
    """
    插件修改参数
    """

    module: str
    """模块"""
    default_status: bool
    """是否默认开启"""
    limit_superuser: bool
    """是否限制超级用户"""
    level: int
    """等级"""
    cost_gold: int
    """花费金币"""
    menu_type: str
    """菜单类型"""
    block_type: BlockType | None = None
    """禁用类型"""
    configs: dict[str, Any] | None = None
    """设置项"""


class PluginInfo(BaseModel):
    """
    基本插件信息
    """

    module: str
    """模块"""
    plugin_name: str
    """插件名称"""
    default_status: bool
    """是否默认开启"""
    limit_superuser: bool
    """是否限制超级用户"""
    level: int
    """等级"""
    cost_gold: int
    """花费金币"""
    menu_type: str
    """菜单类型"""
    version: str
    """版本"""
    status: bool
    """状态"""
    author: str | None = None
    """作者"""
    block_type: BlockType | None = Field(None, description="插件禁用状态 (None: 启用)")
    """禁用状态"""
    is_builtin: bool = False
    """是否为内置插件"""
    allow_switch: bool = True
    """是否允许开关"""
    allow_setting: bool = True
    """是否允许设置"""


class PluginConfig(BaseModel):
    """
    插件配置项
    """

    module: str = Field(..., description="模块名")
    key: str = Field(..., description="键")
    value: Any = Field(None, description="值")
    help: str | None = Field(None, description="帮助信息")
    default_value: Any = Field(None, description="默认值")
    type: str | None = Field(None, description="类型")
    type_inner: list[str] | None = Field(None, description="内部类型")


class PluginCount(BaseModel):
    """
    插件数量
    """

    normal: int = 0
    """普通插件"""
    admin: int = 0
    """管理员插件"""
    superuser: int = 0
    """超级用户插件"""
    other: int = 0
    """其他插件"""


class BatchUpdatePluginItem(BaseModel):
    module: str = Field(..., description="插件模块名")
    default_status: bool | None = Field(None, description="默认状态(开关)")
    menu_type: str | None = Field(None, description="菜单类型")
    block_type: BlockType | None = Field(
        None, description="插件禁用状态 (None: 启用, ALL: 禁用)"
    )


class BatchUpdatePlugins(BaseModel):
    updates: list[BatchUpdatePluginItem] = Field(
        ..., description="要批量更新的插件列表"
    )


class PluginDetail(PluginInfo):
    """
    插件详情
    """

    config_list: list[PluginConfig]


class RenameMenuTypePayload(BaseModel):
    old_name: str = Field(..., description="旧菜单类型名称")
    new_name: str = Field(..., description="新菜单类型名称")


class PluginIr(BaseModel):
    id: int
    """插件id"""


class BatchUpdateResult(BaseModel):
    """
    批量更新插件结果
    """

    success: bool = Field(..., description="是否全部成功")
    """是否全部成功"""
    updated_count: int = Field(..., description="更新成功的数量")
    """更新成功的数量"""
    errors: list[dict[str, str]] = Field(
        default_factory=list, description="错误信息列表"
    )
    """错误信息列表"""
