from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from zhenxun.services import renderer_service
from zhenxun.services.renderer.types import Renderable, RenderResult

from .models.components import (
    Alert,
    Avatar,
    AvatarGroup,
    Badge,
    Divider,
    KpiCard,
    ProgressBar,
    Timeline,
    TimelineItem,
    UserInfoBlock,
)
from .models.core import (
    CardData,
    DetailsData,
    LayoutData,
    ListData,
    MarkdownData,
    NotebookData,
    RenderableComponent,
    TableData,
    TemplateComponent,
    TextData,
)
from .registry import component, create

T_Model = TypeVar("T_Model", bound=BaseModel)


def register_component(namespace: str, template_dir: Path):
    """
    注册一个第三方组件包（命名空间）。
    注册后，可在模板中通过 `@namespace/template.html` 引用。
    """

    renderer_service.register_template_namespace(namespace, template_dir)


def template(path: str | Path, data: dict[str, Any]) -> TemplateComponent:
    """
    创建一个基于独立模板文件的UI组件。
    适用于不遵循标准主题结构，直接渲染单个HTML文件的场景。

    参数:
        path: 指向HTML模板文件的绝对或相对路径
        data: 传递给模板的上下文数据字典

    返回:
        TemplateComponent: 可被 `render()` 函数处理的组件实例
    """
    if isinstance(path, str):
        path = Path(path)

    return TemplateComponent(template_path=path, data=data)


def markdown(content: str = "", style: str | Path | None = "default") -> MarkdownData:
    """
    创建一个基于Markdown内容的UI组件。

    参数:
        content: 要渲染的Markdown字符串。
        style: (可选) Markdown的样式名称（如 'github-light'）或一个指向
               自定义CSS文件的路径。

    返回:
        MarkdownData: Markdown 组件实例
    """
    component = MarkdownData()
    if content:
        component.text(content)

    if isinstance(style, Path):
        component.css_path = str(style.absolute())
    else:
        component.style_name = style
    return component


def table(title: str, tip: str | None = None) -> TableData:
    """
    创建一个表格组件构建器。
    支持链式调用，例如: `ui.table("Title").set_headers(["A", "B"]).add_row([1, 2])`

    参数:
        title: 表格标题
        tip: 标题旁的提示文本（可选）
    """
    return TableData(title=title, tip=tip, headers=[], rows=[])


def notebook(data: list[Any] | None = None) -> NotebookData:
    """
    创建一个 Notebook 文档构建器。

    参数:
        data: 初始元素列表（可选）
    """
    return NotebookData(elements=data or [])  # type: ignore


def alert(
    title: str,
    content: str,
    type: Literal["info", "success", "warning", "error"] = "info",
) -> Alert:
    """创建 Alert 组件"""
    return Alert(
        title=title,
        content=content,
        type=type,
    )


def badge(
    text: str,
    color_scheme: Literal["primary", "success", "warning", "error", "info"] = "info",
) -> Badge:
    """创建 Badge 组件"""
    return Badge(
        text=text,
        color_scheme=color_scheme,
    )


def divider(
    margin: str = "2em 0",
    color: str = "#f7889c",
    style: Literal["solid", "dashed", "dotted"] = "solid",
    thickness: str = "1px",
) -> Divider:
    """创建 Divider 组件"""
    return Divider(
        margin=margin,
        color=color,
        style=style,
        thickness=thickness,
    )


def progress_bar(
    progress: float,
    label: str | None = None,
    color_scheme: Literal["primary", "success", "warning", "error", "info"] = "primary",
    animated: bool = False,
) -> ProgressBar:
    """
    创建 ProgressBar 进度条组件。
    支持多种预设颜色方案和动画效果。
    """
    return ProgressBar(
        progress=progress,
        label=label,
        color_scheme=color_scheme,
        animated=animated,
    )


def kpi_card(label: str, value: Any, **kwargs) -> KpiCard:
    """创建 KPI 卡片"""
    return KpiCard(label=label, value=value, **kwargs)


def text(
    text: str,
    align: Literal["left", "right", "center"] = "left",
    **kwargs,
) -> TextData:
    """
    创建纯文本组件。

    参数:
        text: 文本内容
        align: 对齐方式
        **kwargs: 传递给首个文本片段的样式参数 (如 bold=True, color='red')

    返回:
        TextData: 支持链式调用 .add_span() 的文本模型
    """
    model = TextData(align=align)
    if text:
        model.add_span(text, **kwargs)
    return model


def card(content: RenderableComponent) -> CardData:
    """创建 Card 组件"""
    return CardData(content=content)


def avatar(
    src: str, shape: Literal["circle", "square"] = "circle", size: int = 50
) -> Avatar:
    return Avatar(src=src, shape=shape, size=size)


def avatar_group(
    avatars: list[Avatar | str] | None = None,
    spacing: int = -15,
    max_count: int | None = None,
) -> AvatarGroup:
    """
    创建头像组。
    avatars: 头像对象或URL列表。
    """
    avatar_objs = []
    if avatars:
        for item in avatars:
            if isinstance(item, str):
                avatar_objs.append(Avatar(src=item))  # type: ignore
            else:
                avatar_objs.append(item)
    return AvatarGroup(avatars=avatar_objs, spacing=spacing, max_count=max_count)


def timeline(items: list[dict[str, Any]] | None = None) -> Timeline:
    """
    创建时间轴。
    items: 包含 timestamp, title, content, icon, color 的字典列表。
    """
    timeline_items = []
    if items:
        for item in items:
            timeline_items.append(TimelineItem(**item))
    return Timeline(items=timeline_items)


def user_info_block(
    name: str,
    avatar_url: str,
    subtitle: str | None = None,
    tags: list[str] | None = None,
) -> UserInfoBlock:
    return UserInfoBlock(
        name=name,
        avatar_url=avatar_url,
        subtitle=subtitle,
        tags=tags or [],
    )


def vstack(children: Sequence[Any], **layout_options) -> LayoutData:
    """
    创建一个垂直布局组件。
    """
    layout = LayoutData.column(**layout_options)
    for child in children:
        layout.add_item(child)
    return layout


def hstack(children: Sequence[Any], **layout_options) -> LayoutData:
    """
    创建一个水平布局组件。
    """
    layout = LayoutData.row(**layout_options)
    for child in children:
        layout.add_item(child)
    return layout


async def render(
    component_or_path: Renderable | str | Path,
    data: dict | None = None,
    *,
    template: str | Path | None = None,
    use_cache: bool = False,
    is_page: bool = False,
    **kwargs,
) -> bytes:
    """
    统一的UI渲染入口。
    这是第三方开发者最常用的函数，用于将任何可渲染对象转换为图片。

    用法:
        1. 渲染一个已构建的UI组件: `render(my_builder.build())`
        2. 直接渲染一个模板文件: `render("path/to/template", data={...})`
        3. 动态替换模板: `render(my_data, template="plugins/my_plugin/custom_card")`

    参数:
        component_or_path: 一个 `Renderable` 实例，或一个指向模板文件的
                           `str` 或 `Path` 对象
        data: 当 `component_or_path` 是路径时，必须提供此数据字典
        template: 强制使用的模板路径，覆盖组件默认设置（可选）
        use_cache: 是否启用渲染结果缓存（默认为 False）
        is_page: 标记此次渲染是否为完整页面（自带html/body），默认为 False
        **kwargs: 传递给截图引擎的参数，如 `viewport={"width": 800, "height": 600}`

    返回:
        bytes: PNG图片二进制数据
    """
    component: Renderable
    if isinstance(component_or_path, str | Path):
        if data is None:
            raise ValueError("使用模板路径渲染时必须提供 'data' 参数。")
        component = TemplateComponent(
            template_path=component_or_path, data=data, is_page=is_page
        )
    else:
        component = component_or_path

    if template:
        if isinstance(template, Path):
            template_str = template.as_posix()
        else:
            template_str = str(template).replace("\\", "/")
        if hasattr(component, "template_path"):
            setattr(component, "template_path", template_str)

    if is_page and hasattr(component, "is_page"):
        component.is_page = True

    return await renderer_service.render(component, use_cache=use_cache, **kwargs)


async def render_template(
    path: str | Path,
    data: dict,
    use_cache: bool = False,
    *,
    is_page: bool = True,
    **kwargs,
) -> bytes:
    """
    渲染一个独立的Jinja2模板文件。

    这是一个便捷函数，封装了 render() 函数的调用，提供更简洁的模板渲染接口。

    参数:
        path: 模板文件路径，相对于主题模板目录。
        data: 传递给模板的数据字典。
        use_cache: (可选) 是否启用渲染缓存，默认为 False。
        is_page: (可选) 标记该模板是否为完整页面。默认为 True，因为独立模板通常已
        包含文档骨架。
        **kwargs: 传递给渲染服务的额外参数。

    返回:
        bytes: 渲染后的图片数据。

    异常:
        RenderingError: 渲染失败时抛出。
    """
    return await render(path, data, use_cache=use_cache, is_page=is_page, **kwargs)


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
    component = MarkdownData()
    component.text(md)

    if isinstance(style, Path):
        component.css_path = str(style.absolute())
    else:
        component.style_name = style

    return await render(component, use_cache=use_cache, **kwargs)


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
    from zhenxun.services.renderer.service import RenderContext

    if not renderer_service._initialized:
        await renderer_service.initialize()
    assert renderer_service._theme_manager is not None, "ThemeManager 未初始化"
    assert renderer_service._screenshot_engine is not None, "ScreenshotEngine 未初始化"
    assert renderer_service._template_engine is not None, "TemplateEngine 未初始化"

    context = RenderContext(
        renderer=renderer_service,
        theme_manager=renderer_service._theme_manager,
        template_engine=renderer_service._template_engine,
        screenshot_engine=renderer_service._screenshot_engine,
        component=component,
        use_cache=use_cache,
        render_options=kwargs,
    )
    return await renderer_service._render_component(context)


__all__ = [
    "DetailsData",
    "ListData",
    "alert",
    "avatar",
    "avatar_group",
    "badge",
    "card",
    "component",
    "create",
    "divider",
    "hstack",
    "kpi_card",
    "markdown",
    "notebook",
    "progress_bar",
    "register_component",
    "render",
    "render_full_result",
    "render_markdown",
    "render_template",
    "table",
    "template",
    "text",
    "timeline",
    "user_info_block",
    "vstack",
]
