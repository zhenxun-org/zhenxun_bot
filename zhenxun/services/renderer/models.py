from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class Theme(BaseModel):
    """
    一个封装了所有主题相关信息的模型。
    """

    name: str = Field(..., description="主题名称")
    palette: dict[str, Any] = Field(
        default_factory=dict,
        description="主题的调色板，用于定义CSS变量和Jinja2模板中的颜色常量",
    )
    style_css: str = Field("", description="用于HTML渲染的全局CSS内容")
    assets_dir: Path = Field(..., description="主题的资产目录路径")
    default_assets_dir: Path = Field(
        ..., description="默认主题的资产目录路径，用于资源回退"
    )


class TemplateManifest(BaseModel):
    """
    模板清单模型，用于描述一个模板的元数据。
    """

    name: str = Field(..., description="模板的人类可读名称")
    engine: Literal["html", "markdown"] = Field(
        "html", description="渲染此模板所需的引擎"
    )
    entrypoint: str = Field(
        ..., description="模板的入口文件 (例如 'template.html' 或 'renderer.py')"
    )
    styles: list[str] | str | None = Field(
        None,
        description="此组件依赖的CSS文件路径列表(相对于此manifest文件所在的组件根目录)",
    )
    render_options: dict[str, Any] = Field(
        default_factory=dict, description="传递给渲染引擎的额外选项 (如viewport)"
    )
