"""
渲染器服务的统一类型定义文件。
合并了原 config.py, models.py, protocols.py。
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .engine import BaseScreenshotEngine
    from .service import RendererService
    from .template import JinjaTemplateEngine
    from .theme import ThemeManager


RESERVED_TEMPLATE_KEYS: set[str] = {
    "data",
    "theme",
    "theme_css",
    "extra_css",
    "required_scripts",
    "required_styles",
    "frameless",
}


class Theme(BaseModel):
    """一个封装了所有主题相关信息的模型。"""

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
    """模板清单模型，用于描述一个模板的元数据。"""

    name: str | None = Field(None, description="模板的人类可读名称")
    engine: Literal["html", "markdown"] = Field(
        "html", description="渲染此模板所需的引擎"
    )
    entrypoint: str | None = Field(
        None, description="模板的入口文件 (例如 'template.html')"
    )
    skin: str | None = Field(None, description="默认皮肤")
    styles: list[str] | str | None = Field(
        None,
        description="此组件依赖的CSS文件路径列表(相对于此manifest文件所在的组件根目录)",
    )
    render_options: dict[str, Any] = Field(
        default_factory=dict, description="传递给渲染引擎的额外选项 (如viewport)"
    )


class RenderResult(BaseModel):
    """渲染服务的统一返回类型。"""

    image_bytes: bytes | None = None
    html_content: str | None = None


class Renderable(ABC):
    """定义可被渲染UI组件必须具备的形态。"""

    component_css: str | None
    is_page: bool

    @property
    @abstractmethod
    def template_name(self) -> str:
        """返回用于渲染此组件的Jinja2模板的路径。"""
        ...

    async def prepare(self) -> None:
        """[可选] 生命周期钩子，用于在渲染前执行异步数据获取和预处理。"""
        pass

    @abstractmethod
    def get_children(self) -> Iterable["Renderable"]:
        """返回一个包含所有直接子组件的可迭代对象。"""
        ...

    def get_required_scripts(self) -> list[str]:
        """[可选] 返回此组件所需的JS脚本路径列表。"""
        return []

    def get_required_styles(self) -> list[str]:
        """[可选] 返回此组件所需的CSS样式表路径列表。"""
        return []

    @abstractmethod
    def get_render_data(self) -> dict[str, Any | Awaitable[Any]]:
        """返回一个将传递给模板的数据字典。"""
        ...

    def get_extra_css(self, context: Any) -> str | Awaitable[str]:
        """[可选] 提供额外的CSS。"""
        return ""


class BaseScreenshotEngine(ABC):
    """截图引擎的抽象基类。"""

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    @abstractmethod
    async def render(self, html: str, base_url_path: Path, **render_options) -> bytes:
        raise NotImplementedError


class RenderStrategy(ABC):
    """渲染策略接口。"""

    @abstractmethod
    async def render(self, context: "RenderContext") -> "RenderResult":
        raise NotImplementedError


@dataclass
class RenderContext:
    """单次渲染任务的上下文对象，用于状态传递和缓存。"""

    renderer: "RendererService"
    theme_manager: "ThemeManager"
    template_engine: "JinjaTemplateEngine"
    screenshot_engine: "BaseScreenshotEngine"
    component: Renderable
    use_cache: bool
    render_options: dict[str, Any]
    resolved_template_paths: dict[str, str] = field(default_factory=dict)
    resolved_style_paths: dict[str, Path | None] = field(default_factory=dict)
    collected_asset_styles: set[str] = field(default_factory=set)
    collected_scripts: set[str] = field(default_factory=set)
    collected_inline_css: list[str] = field(default_factory=list)
    processed_components: set[int] = field(default_factory=set)
