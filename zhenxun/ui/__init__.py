from pathlib import Path
from typing import Any

from zhenxun.services.renderer.protocols import Renderable

from . import builders
from .builders.core.layout import LayoutBuilder
from .models.core.base import RenderableComponent
from .models.core.markdown import MarkdownData
from .models.core.template import TemplateComponent


def template(path: str | Path, data: dict[str, Any]) -> TemplateComponent:
    """
    创建一个基于独立模板文件的UI组件。
    适用于不希望遵循标准主题结构，而是直接渲染单个HTML文件的场景。

    参数:
        path: 指向HTML模板文件的绝对或相对路径。
        data: 传递给模板的上下文数据字典。

    返回:
        TemplateComponent: 一个可被 `render()` 函数处理的组件实例。
    """
    if isinstance(path, str):
        path = Path(path)

    return TemplateComponent(template_path=path, data=data)


def markdown(content: str, style: str | Path | None = "default") -> MarkdownData:
    """
    创建一个基于Markdown内容的UI组件。

    参数:
        content: 要渲染的Markdown字符串。
        style: (可选) Markdown的样式名称（如 'github-light'）或一个指向
               自定义CSS文件的路径。

    返回:
        MarkdownData: 一个可被 `render()` 函数处理的组件实例。
    """
    builder = builders.MarkdownBuilder().text(content)
    component = builder.build()
    if isinstance(style, Path):
        component.css_path = str(style.absolute())
    else:
        component.style_name = style
    return component


def vstack(children: list[RenderableComponent], **layout_options) -> "LayoutBuilder":
    """
    创建一个垂直布局组件。
    便捷函数，用于将多个组件垂直堆叠。

    参数:
        children: 一个包含 `RenderableComponent` 实例的列表。
        **layout_options: 传递给布局模板的额外选项，如 `padding`, `gap`。

    返回:
        LayoutBuilder: 一个配置好的垂直布局构建器。
    """
    builder = LayoutBuilder.column(**layout_options)
    for child in children:
        builder.add_item(child)
    return builder


def hstack(children: list[RenderableComponent], **layout_options) -> "LayoutBuilder":
    """
    创建一个水平布局组件。
    便捷函数，用于将多个组件水平排列。

    参数:
        children: 一个包含 `RenderableComponent` 实例的列表。
        **layout_options: 传递给布局模板的额外选项，如 `padding`, `gap`。

    返回:
        LayoutBuilder: 一个配置好的水平布局构建器。
    """
    builder = LayoutBuilder.row(**layout_options)
    for child in children:
        builder.add_item(child)
    return builder


async def render(
    component_or_path: Renderable | str | Path,
    data: dict | None = None,
    *,
    use_cache: bool = False,
    **kwargs,
) -> bytes:
    """
    统一的UI渲染入口。
    这是第三方开发者最常用的函数，用于将任何可渲染对象转换为图片。

    用法:
        1. 渲染一个已构建的UI组件: `render(my_builder.build())`
        2. 直接渲染一个模板文件: `render("path/to/template", data={...})`

    参数:
        component_or_path: 一个 `Renderable` 实例，或一个指向模板文件的
                           `str` 或 `Path` 对象。
        data: (可选) 当 `component_or_path` 是路径时，必须提供此数据字典。
        use_cache: (可选) 是否为此渲染启用文件缓存，默认为 `False`。
        **kwargs: 传递给底层截图引擎的额外参数，例如 `viewport`。

    返回:
        bytes: 渲染后的PNG图片字节数据。
    """
    from zhenxun.services import renderer_service

    component: Renderable
    if isinstance(component_or_path, str | Path):
        if data is None:
            raise ValueError("使用模板路径渲染时必须提供 'data' 参数。")
        component = TemplateComponent(template_path=component_or_path, data=data)
    else:
        component = component_or_path

    return await renderer_service.render(component, use_cache=use_cache, **kwargs)


async def render_template(
    path: str | Path, data: dict, use_cache: bool = False, **kwargs
) -> bytes:
    """
    渲染一个独立的Jinja2模板文件。

    这是一个便捷函数，封装了 render() 函数的调用，提供更简洁的模板渲染接口。

    参数:
        path: 模板文件路径，相对于主题模板目录。
        data: 传递给模板的数据字典。
        use_cache: (可选) 是否启用渲染缓存，默认为 False。
        **kwargs: 传递给渲染服务的额外参数。

    返回:
        bytes: 渲染后的图片数据。

    异常:
        RenderingError: 渲染失败时抛出。
    """
    return await render(path, data, use_cache=use_cache, **kwargs)


async def render_markdown(
    md: str, style: str | Path | None = "default", use_cache: bool = False, **kwargs
) -> bytes:
    """
    将Markdown字符串渲染为图片。

    这是一个便捷函数，封装了 render() 函数的调用，专门用于渲染Markdown内容。

    参数:
        md: 要渲染的Markdown内容字符串。
        style: (可选) 样式名称或自定义CSS文件路径，默认为 "default"。
        use_cache: (可选) 是否启用渲染缓存，默认为 False。
        **kwargs: 传递给渲染服务的额外参数。

    返回:
        bytes: 渲染后的图片数据。

    异常:
        RenderingError: 渲染失败时抛出。
    """
    builder = builders.MarkdownBuilder().text(md)
    component = builder.build()
    if isinstance(style, Path):
        component.css_path = str(style.absolute())
    else:
        component.style_name = style

    return await render(component, use_cache=use_cache, **kwargs)


from zhenxun.services.renderer.protocols import RenderResult


async def render_full_result(
    component: Renderable, use_cache: bool = False, **kwargs
) -> RenderResult:
    """
    渲染组件并返回包含图片和HTML的完整结果对象。
    主要用于调试或需要同时访问图片和其源HTML的场景。

    参数:
        component: 一个 `Renderable` 实例。
        use_cache: (可选) 是否为此渲染启用文件缓存，默认为 `False`。
        **kwargs: 传递给底层截图引擎的额外参数。

    返回:
        RenderResult: 一个包含 `image_bytes` 和 `html_content` 的Pydantic模型。
    """
    from zhenxun.services import renderer_service
    from zhenxun.services.renderer.service import RenderContext

    if not renderer_service._initialized:
        await renderer_service.initialize()
    assert renderer_service._theme_manager is not None, "ThemeManager 未初始化"
    assert renderer_service._screenshot_engine is not None, "ScreenshotEngine 未初始化"

    context = RenderContext(
        renderer=renderer_service,
        theme_manager=renderer_service._theme_manager,
        screenshot_engine=renderer_service._screenshot_engine,
        component=component,
        use_cache=use_cache,
        render_options=kwargs,
    )
    return await renderer_service._render_component(context)


__all__ = [
    "builders",
    "hstack",
    "markdown",
    "render",
    "render_full_result",
    "render_markdown",
    "render_template",
    "template",
    "vstack",
]
