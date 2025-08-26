from abc import ABC, abstractmethod
from collections.abc import Awaitable, Iterable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel


class Renderable(ABC):
    """
    一个协议，定义了任何可被渲染的UI组件必须具备的形态。

    该协议确保了所有UI组件都能被 `RendererService` 以统一的方式处理。
    任何想要被渲染服务处理的UI数据模型都应直接或间接实现此协议。
    """

    component_css: str | None

    @property
    @abstractmethod
    def template_name(self) -> str:
        """
        返回用于渲染此组件的Jinja2模板的路径。
        这是一个抽象属性，所有子类都必须覆盖它。

        返回:
            str: 指向模板文件的相对路径，例如 'components/core/table'。
        """
        ...

    async def prepare(self) -> None:
        """
        [可选] 一个生命周期钩子，用于在渲染前执行异步数据获取和预处理。

        此方法会在组件的数据被传递给模板之前调用。
        适合用于执行数据库查询、网络请求等耗时操作，以准备最终的渲染数据。
        """
        pass

    @abstractmethod
    def get_children(self) -> Iterable["Renderable"]:
        """
        [新增] 返回一个包含所有直接子组件的可迭代对象。

        这使得渲染服务能够递归地遍历整个组件树，以执行依赖收集（CSS、JS）等任务。
        非容器组件应返回一个空列表。
        """
        ...

    def get_required_scripts(self) -> list[str]:
        """[可选] 返回此组件所需的JS脚本路径列表 (相对于主题的assets目录)。"""
        return []

    def get_required_styles(self) -> list[str]:
        """[可选] 返回此组件所需的CSS样式表路径列表 (相对于主题的assets目录)。"""
        return []

    @abstractmethod
    def get_render_data(self) -> dict[str, Any | Awaitable[Any]]:
        """
        返回一个将传递给模板的数据字典。
        重要：字典的值可以是协程(Awaitable)，渲染服务会自动解析它们。

        返回:
            dict[str, Any | Awaitable[Any]]: 用于模板渲染的上下文数据。
        """
        ...

    def get_extra_css(self, context: Any) -> str | Awaitable[str]:
        """
        [可选] 一个生命周期钩子，让组件可以提供额外的CSS。
        可以返回 str 或 awaitable[str]。

        参数:
            context: 当前的渲染上下文对象，可用于访问主题管理器等。

        返回:
            str | Awaitable[str]: 注入到页面的额外CSS字符串。
        """
        return ""


class ScreenshotEngine(Protocol):
    """
    一个协议，定义了截图引擎的核心能力。
    这允许系统在不同的截图后端（如Playwright, Pyppeteer）之间切换，
    而无需修改上层渲染服务的代码。
    """

    async def render(self, html: str, base_url_path: Path, **render_options) -> bytes:
        """
        将HTML字符串截图为图片。

        参数:
            html: 要渲染的HTML内容。
            base_url_path: 用于解析相对路径（如CSS, JS, 图片）的基础URL路径。
            **render_options: 传递给底层截图库的额外选项 (如 viewport)。

        返回:
            bytes: 渲染后的图片字节数据。
        """
        ...


class RenderResult(BaseModel):
    """
    渲染服务的统一返回类型。
    封装了渲染过程可能产出的所有结果，主要用于调试和内部传递。
    """

    image_bytes: bytes | None = None
    html_content: str | None = None
