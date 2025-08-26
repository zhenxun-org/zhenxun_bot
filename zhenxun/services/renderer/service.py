import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import hashlib
import inspect
from pathlib import Path
from typing import Any, ClassVar

import aiofiles
from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PrefixLoader,
    TemplateNotFound,
    select_autoescape,
)
from nonebot.utils import is_coroutine_callable
import ujson as json

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import THEMES_PATH, UI_CACHE_PATH
from zhenxun.services.log import logger
from zhenxun.utils.exception import RenderingError
from zhenxun.utils.pydantic_compat import _dump_pydantic_obj

from .config import RESERVED_TEMPLATE_KEYS
from .engine import get_screenshot_engine
from .protocols import Renderable, RenderResult, ScreenshotEngine
from .registry import asset_registry
from .theme import RelativePathEnvironment, ThemeManager


@dataclass
class RenderContext:
    """单次渲染任务的上下文对象，用于状态传递和缓存。"""

    renderer: "RendererService"
    theme_manager: ThemeManager
    screenshot_engine: ScreenshotEngine
    component: Renderable
    use_cache: bool
    render_options: dict[str, Any]
    resolved_template_paths: dict[str, str] = field(default_factory=dict)
    resolved_style_paths: dict[str, Path | None] = field(default_factory=dict)
    collected_asset_styles: set[str] = field(default_factory=set)
    collected_scripts: set[str] = field(default_factory=set)
    collected_inline_css: list[str] = field(default_factory=list)
    processed_components: set[int] = field(default_factory=set)


class RendererService:
    """
    图片渲染服务的统一门面。

    作为UI渲染的中心枢纽，负责编排和调用底层服务，提供统一的渲染接口。
    主要职责包括：
    - 管理和加载UI主题 (通过 ThemeManager)。
    - 使用Jinja2引擎将组件数据模型 (`Renderable`) 渲染为HTML。
    - 调用截图引擎 (ScreenshotEngine) 将HTML转换为图片。
    - 处理插件注册的模板、过滤器和全局函数。
    - (可选) 管理渲染结果的缓存。
    """

    _plugin_template_paths: ClassVar[dict[str, Path]] = {}

    def __init__(self):
        self._jinja_env: Environment | None = None
        self._theme_manager: ThemeManager | None = None
        self._screenshot_engine: ScreenshotEngine | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._custom_filters: dict[str, Callable] = {}
        self._custom_globals: dict[str, Callable] = {}

        self.filter("dump_json")(self._pydantic_tojson_filter)

    def _create_jinja_env(self) -> Environment:
        """
        创建并配置 Jinja2 渲染环境。

        构建一个完整的 Jinja2 环境，包含：
        - PrefixLoader：用于插件模板的命名空间加载
        - FileSystemLoader：用于主题模板的文件系统加载
        - RelativePathEnvironment：支持模板间相对路径引用的自定义环境

        返回:
            Environment: 完全配置好的 Jinja2 环境实例，准备接收自定义过滤器和全局函数。
        """
        prefix_loader = PrefixLoader(
            {
                namespace: FileSystemLoader(str(path.absolute()))
                for namespace, path in self._plugin_template_paths.items()
            }
        )
        theme_loader = FileSystemLoader(str(THEMES_PATH / "default"))
        final_loader = ChoiceLoader([prefix_loader, theme_loader])

        env = RelativePathEnvironment(
            loader=final_loader,
            enable_async=True,
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        return env

    def register_template_namespace(self, namespace: str, path: Path):
        """
        为插件注册一个Jinja2模板命名空间。

        这允许插件在自己的目录中维护模板，并通过
        `{% include '@namespace/template.html' %}` 的方式引用它们，
        避免了与核心或其他插件的模板命名冲突。

        参数:
            namespace: 插件的唯一命名空间，例如插件名。
            path: 包含该插件模板的目录路径。
        """
        if namespace in self._plugin_template_paths:
            logger.warning(f"模板命名空间 '{namespace}' 已被注册，将被覆盖。")
        if not path.is_dir():
            raise ValueError(f"提供的路径 '{path}' 不是一个有效的目录。")
        self._plugin_template_paths[namespace] = path

    def register_markdown_style(self, name: str, path: Path):
        """
        为 Markdown 渲染器注册一个具名样式 (委托给 AssetRegistry)。

        参数:
            name (str): 样式的唯一名称，例如 'cyberpunk'。
            path (Path): 指向该样式的CSS文件路径。
        """
        if not path.is_file():
            raise ValueError(f"提供的路径 '{path}' 不是一个有效的 CSS 文件。")
        asset_registry.register_markdown_style(name, path)

    def filter(self, name: str) -> Callable:
        """
        装饰器：注册一个自定义 Jinja2 过滤器。

        参数:
            name: 过滤器在模板中的调用名称。

        返回:
            Callable: 用于装饰过滤器函数的装饰器。
        """

        def decorator(func: Callable) -> Callable:
            if name in self._custom_filters:
                logger.warning(f"Jinja2 过滤器 '{name}' 已被注册，将被覆盖。")
            self._custom_filters[name] = func
            logger.debug(f"已注册自定义 Jinja2 过滤器: '{name}'")
            return func

        return decorator

    def global_function(self, name: str) -> Callable:
        """
        装饰器：注册一个自定义 Jinja2 全局函数。

        参数:
            name: 函数在模板中的调用名称。

        返回:
            Callable: 用于装饰全局函数的装饰器。
        """

        def decorator(func: Callable) -> Callable:
            if name in self._custom_globals:
                logger.warning(f"Jinja2 全局函数 '{name}' 已被注册，将被覆盖。")
            self._custom_globals[name] = func
            logger.debug(f"已注册自定义 Jinja2 全局函数: '{name}'")
            return func

        return decorator

    async def initialize(self):
        """
        [新增] 延迟初始化方法，在 on_startup 钩子中调用。

        负责初始化截图引擎和主题管理器，确保在首次渲染前所有依赖都已准备就绪。
        使用锁来防止并发初始化。
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return

            self._jinja_env = self._create_jinja_env()

            self._jinja_env.filters.update(self._custom_filters)
            self._jinja_env.globals.update(self._custom_globals)

            self._screenshot_engine = get_screenshot_engine()

            self._theme_manager = ThemeManager(self._jinja_env)

            current_theme_name = Config.get_config("UI", "THEME", "default")
            await self._theme_manager.load_theme(current_theme_name)
            self._initialized = True

    async def _collect_dependencies_recursive(
        self, component: Renderable, context: "RenderContext"
    ):
        """
        递归遍历组件树，收集所有依赖项（CSS, JS, 额外CSS）并存入上下文。

        这是实现组件化样式和脚本管理的基础，确保即使是深层嵌套的组件
        所需的资源也能被正确加载到最终的HTML页面中。
        """
        component_id = id(component)
        if component_id in context.processed_components:
            return
        context.processed_components.add(component_id)

        component_path_base = str(component.template_name)
        manifest = await context.theme_manager.get_template_manifest(
            component_path_base
        )

        style_paths_to_load = []
        if manifest and manifest.styles:
            styles = (
                [manifest.styles]
                if isinstance(manifest.styles, str)
                else manifest.styles
            )
            for style_path in styles:
                full_style_path = str(Path(component_path_base) / style_path).replace(
                    "\\", "/"
                )
                style_paths_to_load.append(full_style_path)
        else:
            resolved_template_name = (
                await context.theme_manager._resolve_component_template(
                    component, context
                )
            )
            conventional_style_path = str(
                Path(resolved_template_name).with_name("style.css")
            ).replace("\\", "/")
            style_paths_to_load.append(conventional_style_path)

        for css_template_path in style_paths_to_load:
            try:
                css_template = context.theme_manager.jinja_env.get_template(
                    css_template_path
                )
                theme_context = {
                    "theme": context.theme_manager.jinja_env.globals.get("theme", {})
                }
                css_content = await css_template.render_async(**theme_context)
                context.collected_inline_css.append(css_content)
            except TemplateNotFound:
                pass

        context.collected_scripts.update(component.get_required_scripts())
        context.collected_asset_styles.update(component.get_required_styles())

        if hasattr(component, "get_extra_css"):
            res = component.get_extra_css(context)
            css_str = await res if inspect.isawaitable(res) else str(res)
            if css_str:
                context.collected_inline_css.append(css_str)

        for child in component.get_children():
            if child:
                await self._collect_dependencies_recursive(child, context)

    async def _render_component(
        self,
        context: "RenderContext",
    ) -> RenderResult:
        """
        核心的私有渲染方法，执行完整的渲染流程。

        执行步骤:
        1.  **缓存检查**: 如果启用缓存，则根据组件模板名和渲染数据生成缓存键，
            并尝试从文件系统中读取缓存图片。
        2.  **组件准备**: 调用 `component.prepare()` 生命周期钩子，允许组件执行
            异步数据加载。
        3.  **依赖收集**: 调用 `_collect_dependencies_recursive` 遍历组件树，
            收集所有需要的CSS文件、JS文件和内联CSS。
        4.  **HTML渲染**: 调用 `ThemeManager` 将组件数据模型渲染为HTML字符串。
            此步骤会处理独立模板和主题内模板两种情况。
        5.  **截图**: 调用 `ScreenshotEngine` 将生成的HTML转换为图片字节。
        6.  **缓存写入**: 如果缓存未命中且启用了缓存，将生成的图片写入文件系统。
        """
        return await self._apply_caching_layer(self._render_component_core, context)

    async def _apply_caching_layer(
        self,
        core_render_func: Callable[..., Awaitable[RenderResult]],
        context: "RenderContext",
    ) -> RenderResult:
        """
        一个高阶函数，为核心渲染逻辑提供缓存层。
        它负责处理缓存的读取和写入，而将实际的渲染工作委托给传入的函数。
        """
        cache_path = None
        component = context.component

        if Config.get_config("UI", "CACHE") and context.use_cache:
            try:
                template_name = component.template_name
                data_dict = component.get_render_data()
                resolved_data_dict = {}
                for key, value in data_dict.items():
                    if is_coroutine_callable(value):  # type: ignore
                        resolved_data_dict[key] = await value
                    else:
                        resolved_data_dict[key] = value
                data_str = json.dumps(resolved_data_dict, sort_keys=True)
                cache_key_str = f"{template_name}:{data_str}"
                cache_filename = (
                    f"{hashlib.sha256(cache_key_str.encode()).hexdigest()}.png"
                )
                cache_path = UI_CACHE_PATH / cache_filename

                if cache_path.exists():
                    logger.debug(f"UI缓存命中: {cache_path}")
                    async with aiofiles.open(cache_path, "rb") as f:
                        image_bytes = await f.read()
                    return RenderResult(
                        image_bytes=image_bytes, html_content="<!-- from cache -->"
                    )
                logger.debug(f"UI缓存未命中: {cache_key_str[:100]}...")
            except Exception as e:
                logger.warning(f"UI缓存读取失败: {e}", e=e)
                cache_path = None

        result = await core_render_func(context)

        if (
            Config.get_config("UI", "CACHE")
            and context.use_cache
            and cache_path
            and result.image_bytes
        ):
            try:
                async with aiofiles.open(cache_path, "wb") as f:
                    await f.write(result.image_bytes)
                logger.debug(f"UI缓存写入成功: {cache_path}")
            except Exception as e:
                logger.warning(f"UI缓存写入失败: {e}", e=e)

        return result

    async def _render_component_core(self, context: "RenderContext") -> RenderResult:
        """
        纯粹的核心渲染逻辑，不包含任何缓存处理。
        此方法负责从组件数据模型生成最终的图片字节和HTML。
        """
        component = context.component

        try:
            if not self._initialized:
                await self.initialize()
            assert context.theme_manager is not None, "ThemeManager 未初始化"
            assert context.screenshot_engine is not None, "ScreenshotEngine 未初始化"

            if (
                hasattr(component, "template_path")
                and isinstance(
                    template_path := getattr(component, "template_path"),
                    Path,
                )
                and template_path.is_absolute()
            ):
                await component.prepare()

                logger.debug(f"正在渲染独立模板: '{template_path}'", "RendererService")

                template_dir = template_path.parent
                temp_loader = FileSystemLoader(str(template_dir))
                temp_env = Environment(
                    loader=temp_loader,
                    enable_async=True,
                    autoescape=select_autoescape(["html", "xml"]),
                )

                temp_env.globals.update(context.theme_manager.jinja_env.globals)
                temp_env.globals["asset"] = (
                    context.theme_manager._create_standalone_asset_loader(template_dir)
                )
                temp_env.filters["md"] = context.theme_manager._markdown_filter

                data_dict = component.get_render_data()
                template = temp_env.get_template(template_path.name)

                template_context = {
                    "theme": context.theme_manager.jinja_env.globals.get("theme", {}),
                    "data": data_dict,
                }
                for key, value in data_dict.items():
                    if key in RESERVED_TEMPLATE_KEYS:
                        logger.warning(
                            f"模板数据键 '{key}' 与渲染器保留关键字冲突，"
                            f"在模板 '{component.template_name}' 中请使用 "
                            f"'data.{key}' 访问。"
                        )
                    else:
                        template_context[key] = value
                html_content = await template.render_async(**template_context)

                component_render_options = data_dict.get("render_options", {})
                if not isinstance(component_render_options, dict):
                    component_render_options = {}

                final_render_options = component_render_options.copy()
                final_render_options.update(context.render_options)

                image_bytes = await context.screenshot_engine.render(
                    html=html_content,
                    base_url_path=template_dir,
                    **final_render_options,
                )

                return RenderResult(image_bytes=image_bytes, html_content=html_content)

            else:
                await component.prepare()
                await self._collect_dependencies_recursive(component, context)

                data_dict = component.get_render_data()
                component_render_options = data_dict.get("render_options", {})
                if not isinstance(component_render_options, dict):
                    component_render_options = {}

                manifest_options = {}
                if manifest := await context.theme_manager.get_template_manifest(
                    component.template_name
                ):
                    manifest_options = manifest.render_options or {}

                final_render_options = component_render_options.copy()
                final_render_options.update(manifest_options)
                final_render_options.update(context.render_options)

                if not context.theme_manager.current_theme:
                    raise RenderingError("渲染失败：主题未被正确加载。")

                html_content = await context.theme_manager._render_component_to_html(
                    context,
                    **final_render_options,
                )

                screenshot_options = final_render_options.copy()
                screenshot_options.pop("extra_css", None)
                screenshot_options.pop("frameless", None)

                image_bytes = await context.screenshot_engine.render(
                    html=html_content,
                    base_url_path=THEMES_PATH.parent,
                    **screenshot_options,
                )

                return RenderResult(image_bytes=image_bytes, html_content=html_content)

        except Exception as e:
            logger.error(
                f"渲染组件 '{component.__class__.__name__}' 时发生错误",
                "RendererService",
                e=e,
            )
            raise RenderingError(
                f"渲染组件 '{component.__class__.__name__}' 失败"
            ) from e

    async def render(
        self,
        component: Renderable,
        use_cache: bool = False,
        **render_options,
    ) -> bytes:
        """
        统一的、多态的渲染入口，直接返回图片字节。

        参数:
            component: 一个 `Renderable` 实例 (例如通过 `TableBuilder().build()` 创建)。
            use_cache: (可选) 是否启用渲染缓存，默认为 False。
            **render_options: 传递给底层截图引擎的额外参数，例如 `viewport`。

        返回:
            bytes: 渲染后的PNG图片字节数据。

        异常:
            RenderingError: 当渲染流程中任何步骤失败时抛出。
        """
        if not self._initialized:
            await self.initialize()
        assert self._theme_manager is not None, "ThemeManager 未初始化"
        assert self._screenshot_engine is not None, "ScreenshotEngine 未初始化"

        context = RenderContext(
            renderer=self,
            theme_manager=self._theme_manager,
            screenshot_engine=self._screenshot_engine,
            component=component,
            use_cache=use_cache,
            render_options=render_options,
        )
        result = await self._render_component(context)
        if Config.get_config("UI", "DEBUG_MODE") and result.html_content:
            logger.info(
                f"--- [UI DEBUG] HTML for {component.__class__.__name__} ---\n"
                f"{result.html_content}\n"
                f"--- [UI DEBUG] End of HTML ---"
            )
        if result.image_bytes is None:
            raise RenderingError("渲染成功但未能生成图片字节数据。")
        return result.image_bytes

    async def render_to_html(
        self, component: Renderable, frameless: bool = False
    ) -> str:
        """
        调试方法：只执行到HTML生成步骤，不进行截图。

        参数:
            component: 一个 `Renderable` 实例。
            frameless: 是否以无边框模式渲染（只渲染HTML片段）。

        返回:
            str: 最终渲染出的完整HTML字符串。
        """
        if not self._initialized:
            await self.initialize()
        assert self._theme_manager is not None, "ThemeManager 未初始化"
        assert self._screenshot_engine is not None, "ScreenshotEngine 未初始化"

        context = RenderContext(
            renderer=self,
            theme_manager=self._theme_manager,
            screenshot_engine=self._screenshot_engine,
            component=component,
            use_cache=False,
            render_options={"frameless": frameless},
        )
        await self._collect_dependencies_recursive(component, context)
        return await self._theme_manager._render_component_to_html(
            context, frameless=frameless
        )

    async def reload_theme(self) -> str:
        """
        重新加载当前主题的配置和样式，并清除缓存的Jinja环境。
        这在开发主题时非常有用，可以热重载主题更改。

        返回:
            str: 已成功加载的主题名称。
        """
        if not self._initialized:
            await self.initialize()
        assert self._theme_manager is not None, "ThemeManager 未初始化"

        current_theme_name = Config.get_config("UI", "THEME", "default")
        await self._theme_manager.load_theme(current_theme_name)
        logger.info(f"主题 '{current_theme_name}' 已成功重载。")
        return current_theme_name

    def list_available_themes(self) -> list[str]:
        """获取所有可用主题的列表。"""
        if not self._initialized or not self._theme_manager:
            raise RuntimeError("ThemeManager尚未初始化。")
        return self._theme_manager.list_available_themes()

    async def switch_theme(self, theme_name: str) -> str:
        """
        切换UI主题，加载新主题并持久化配置。

        返回:
            str: 已成功切换到的主题名称。
        """
        if not self._initialized or not self._theme_manager:
            await self.initialize()
        assert self._theme_manager is not None

        available_themes = self._theme_manager.list_available_themes()
        if theme_name not in available_themes:
            raise FileNotFoundError(
                f"主题 '{theme_name}' 不存在。可用主题: {', '.join(available_themes)}"
            )

        await self._theme_manager.load_theme(theme_name)
        Config.set_config("UI", "THEME", theme_name, auto_save=True)
        logger.info(f"UI主题已切换为: {theme_name}")
        return theme_name

    @staticmethod
    def _pydantic_tojson_filter(obj: Any) -> str:
        """一个能够递归处理Pydantic模型及其集合的 tojson 过滤器"""
        dumped_obj = _dump_pydantic_obj(obj)
        return json.dumps(dumped_obj, ensure_ascii=False)
