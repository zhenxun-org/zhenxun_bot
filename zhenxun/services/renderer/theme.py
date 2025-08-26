from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiofiles
from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PrefixLoader,
    TemplateNotFound,
    pass_context,
)
import markdown
from markupsafe import Markup
from pydantic import BaseModel
import ujson as json

from zhenxun.configs.path_config import THEMES_PATH
from zhenxun.services.log import logger
from zhenxun.services.renderer.models import TemplateManifest
from zhenxun.services.renderer.protocols import Renderable
from zhenxun.services.renderer.registry import asset_registry
from zhenxun.utils.pydantic_compat import model_dump

if TYPE_CHECKING:
    from .service import RenderContext

from .config import RESERVED_TEMPLATE_KEYS


class RelativePathEnvironment(Environment):
    """
    一个自定义的 Jinja2 环境，重写了 join_path 方法以支持模板间的相对路径引用。
    """

    def join_path(self, template: str, parent: str) -> str:
        """
        如果模板路径以 './' 或 '../' 开头，则视为相对于父模板的路径进行解析。
        否则，使用默认的解析行为。
        """
        if template.startswith("./") or template.startswith("../"):
            path = os.path.normpath(os.path.join(os.path.dirname(parent), template))
            return path.replace(os.path.sep, "/")
        return super().join_path(template, parent)


class Theme(BaseModel):
    name: str
    palette: dict[str, Any]
    style_css: str = ""
    assets_dir: Path
    default_assets_dir: Path


class ThemeManager:
    def __init__(self, env: Environment):
        """
        主题管理器，负责UI主题的加载、解析和模板渲染。

        主要职责:
        - 加载和管理UI主题，包括 `palette.json` (调色板) 和 `theme.css.jinja`(主题样式)
        - 配置和持有核心的 Jinja2 环境实例。
        - 向 Jinja2 环境注入全局函数，如 `asset()` 和 `render()`，供模板使用。
        - 实现`asset()`函数的资源解析逻辑，支持皮肤、组件、主题和默认主题之间的资源回退
        - 封装将 `Renderable` 组件渲染为最终HTML的复杂逻辑。
        """
        self.jinja_env = env
        self.current_theme: Theme | None = None

        self.jinja_env.globals["render"] = self._global_render_component
        self.jinja_env.globals["asset"] = self._create_asset_loader()
        self.jinja_env.globals["resolve_template"] = self._resolve_component_template

        self.jinja_env.filters["md"] = self._markdown_filter

    def list_available_themes(self) -> list[str]:
        """扫描主题目录并返回所有可用的主题名称。"""
        if not THEMES_PATH.is_dir():
            return []
        return [d.name for d in THEMES_PATH.iterdir() if d.is_dir()]

    def _find_component_root(self, start_path: Path) -> Path:
        """
        从给定的起始路径向上查找，直到找到包含 manifest.json 的目录。
        这被认为是组件的根目录。如果找不到，则返回起始路径的目录。
        """
        current_path = start_path.parent
        for _ in range(len(current_path.parts)):
            if (current_path / "manifest.json").exists():
                return current_path
            if current_path.parent == current_path:
                break
            current_path = current_path.parent
        return start_path.parent

    def _create_asset_loader(
        self, local_base_path: Path | None = None
    ) -> Callable[..., str]:
        """
        创建并返回一个用于解析静态资源的闭包函数 (Jinja2中的 `asset()` 函数)。

        该函数实现了强大的资源解析回退逻辑，查找顺序如下:
        1.  **相对路径 (`./`)**: 优先查找相对于当前模板的 `assets` 目录。
            - 这支持组件皮肤 (`skins/`) 对其资源的覆盖。
        2.  **当前主题**: 在当前激活主题的 `assets` 目录中查找。
        3.  **默认主题**: 如果当前主题未找到，则回退到 `default` 主题的 `assets` 目录。

        参数:
            local_base_path: (可选) 当渲染独立模板时，提供模板所在的目录。
        """

        @pass_context
        def asset_loader(ctx, asset_path: str) -> str:
            if asset_path.startswith("./"):
                parent_template_name = ctx.environment.get_template(ctx.name).name
                parent_template_abs_path = Path(
                    ctx.environment.loader.get_source(
                        ctx.environment, parent_template_name
                    )[1]
                )

                if (
                    "/skins/" in parent_template_abs_path.as_posix()
                    or "\\skins\\" in parent_template_abs_path.as_posix()
                ):
                    skin_dir = parent_template_abs_path.parent
                    skin_asset_path = skin_dir / "assets" / asset_path[2:]
                    if skin_asset_path.exists():
                        logger.debug(f"找到皮肤本地资源: '{skin_asset_path}'")
                        return skin_asset_path.absolute().as_uri()
                    logger.debug(
                        f"皮肤本地资源未找到: '{skin_asset_path}',将回退到组件公共资源"
                    )

                component_root = self._find_component_root(parent_template_abs_path)

                local_asset = component_root / "assets" / asset_path[2:]
                if local_asset.exists():
                    logger.debug(f"找到组件公共资源: '{local_asset}'")
                    return local_asset.absolute().as_uri()

                logger.warning(
                    f"组件相对资源未找到: '{asset_path}'。已在皮肤和组件根目录中查找。"
                )
                return ""

            assert self.current_theme is not None
            current_theme_asset = self.current_theme.assets_dir / asset_path
            if current_theme_asset.exists():
                return current_theme_asset.absolute().as_uri()

            default_theme_asset = self.current_theme.default_assets_dir / asset_path
            if default_theme_asset.exists():
                return default_theme_asset.absolute().as_uri()

            logger.warning(
                f"资源文件在主题 '{self.current_theme.name}' 和 'default' 中均未找到: "
                f"{asset_path}"
            )
            return ""

        return asset_loader

    def _create_standalone_asset_loader(
        self, local_base_path: Path
    ) -> Callable[[str], str]:
        """
        [新增] 为独立模板创建一个专用的、更简单的 asset loader。
        """

        def asset_loader(asset_path: str) -> str:
            if asset_path.startswith("./"):
                local_file = local_base_path / "assets" / asset_path[2:]
                if local_file.exists():
                    return local_file.absolute().as_uri()
                logger.warning(
                    f"独立模板本地资源 '{asset_path}' 在 "
                    f"'{local_base_path / 'assets'}' 中未找到。"
                )
                return ""

            assert self.current_theme is not None
            current_theme_asset = self.current_theme.assets_dir / asset_path
            if current_theme_asset.exists():
                return current_theme_asset.absolute().as_uri()

            default_theme_asset = self.current_theme.default_assets_dir / asset_path
            if default_theme_asset.exists():
                return default_theme_asset.absolute().as_uri()

            logger.warning(
                f"资源文件在主题 '{self.current_theme.name}' 和 'default' 中均未找到: "
                f"{asset_path}"
            )
            return ""

        return asset_loader

    async def _global_render_component(self, component: Renderable | None) -> str:
        """
        一个全局的Jinja2函数，用于在模板内部渲染子组件
        它封装了查找模板、设置上下文和渲染的逻辑。
        """
        if not component:
            return ""
        try:

            class MockContext:
                def __init__(self):
                    self.resolved_template_paths = {}
                    self.theme_manager = self

            mock_context = MockContext()
            template_path = await self._resolve_component_template(
                component,
                mock_context,  # type: ignore
            )
            template = self.jinja_env.get_template(template_path)

            template_context = {
                "data": component,
                "frameless": True,
            }
            render_data = component.get_render_data()
            template_context.update(render_data)

            return Markup(await template.render_async(**template_context))
        except Exception as e:
            logger.error(
                f"在全局 render 函数中渲染组件 '{component.__class__.__name__}' 失败",
                e=e,
            )
            return f"<!-- 组件渲染失败{component.__class__.__name__}: {e} -->"

    @staticmethod
    def _markdown_filter(text: str) -> str:
        """一个将 Markdown 文本转换为 HTML 的 Jinja2 过滤器。"""
        if not isinstance(text, str):
            return ""
        return markdown.markdown(
            text,
            extensions=[
                "pymdownx.tasklist",
                "tables",
                "fenced_code",
                "codehilite",
                "mdx_math",
                "pymdownx.tilde",
            ],
            extension_configs={"mdx_math": {"enable_dollar_delimiter": True}},
        )

    async def load_theme(self, theme_name: str = "default"):
        theme_dir = THEMES_PATH / theme_name
        if not theme_dir.is_dir():
            logger.error(f"主题 '{theme_name}' 不存在，将回退到默认主题。")
            if theme_name == "default":
                raise FileNotFoundError("默认主题 'default' 未找到！")
            theme_name = "default"
            theme_dir = THEMES_PATH / "default"

        default_palette_path = THEMES_PATH / "default" / "palette.json"
        default_palette = (
            json.loads(default_palette_path.read_text("utf-8"))
            if default_palette_path.exists()
            else {}
        )
        if self.jinja_env.loader and isinstance(self.jinja_env.loader, ChoiceLoader):
            current_loaders = list(self.jinja_env.loader.loaders)
            if len(current_loaders) > 1 and isinstance(
                current_loaders[0], PrefixLoader
            ):
                prefix_loader = current_loaders[0]
                new_theme_loader = FileSystemLoader(
                    [str(theme_dir), str(THEMES_PATH / "default")]
                )
                self.jinja_env.loader = ChoiceLoader([prefix_loader, new_theme_loader])
            else:
                self.jinja_env.loader = FileSystemLoader(
                    [str(theme_dir), str(THEMES_PATH / "default")]
                )
        else:
            self.jinja_env.loader = FileSystemLoader(
                [str(theme_dir), str(THEMES_PATH / "default")]
            )

        palette_path = theme_dir / "palette.json"
        palette = (
            json.loads(palette_path.read_text("utf-8")) if palette_path.exists() else {}
        )

        self.current_theme = Theme(
            name=theme_name,
            palette=palette,
            assets_dir=theme_dir / "assets",
            default_assets_dir=THEMES_PATH / "default" / "assets",
        )
        theme_context_dict = {
            "name": theme_name,
            "palette": palette,
            "assets_dir": theme_dir / "assets",
            "default_assets_dir": THEMES_PATH / "default" / "assets",
        }
        self.jinja_env.globals["theme"] = theme_context_dict
        self.jinja_env.globals["default_theme_palette"] = default_palette
        logger.info(f"主题管理器已加载主题: {theme_name}")

    async def _resolve_component_template(
        self, component: Renderable, context: "RenderContext"
    ) -> str:
        """
        智能解析组件模板的路径，支持简单组件和带皮肤(variant)的复杂组件。

        查找顺序如下:
        1.  **带皮肤的组件**: 如果组件定义了 `variant`，则在
            `components/{component_name}/skins/{variant_name}/` 目录下查找入口文件。
        2.  **标准组件**: 在组件的根目录 `components/{component_name}/` 下查找入口文件。
        3.  **兼容模式**: (作为最终回退)直接查找名为`components/{component_name}.html`
            的文件

        入口文件名默认为 `main.html`，但可以被组件目录下的 `manifest.json` 文件中的
        `entrypoint` 字段覆盖。
        """
        component_path_base = str(component.template_name)

        variant = getattr(component, "variant", None)
        cache_key = f"{component_path_base}::{variant or 'default'}"
        if cached_path := context.resolved_template_paths.get(cache_key):
            logger.trace(f"模板路径缓存命中: '{cache_key}' -> '{cached_path}'")
            return cached_path

        if Path(component_path_base).suffix:
            try:
                self.jinja_env.get_template(component_path_base)
                logger.debug(f"解析到直接模板路径: '{component_path_base}'")
                return component_path_base
            except TemplateNotFound as e:
                logger.error(f"指定的模板文件路径不存在: '{component_path_base}'", e=e)
                raise e

        entrypoint_filename = "main.html"
        manifest = await self.get_template_manifest(component_path_base)
        if manifest and manifest.entrypoint:
            entrypoint_filename = manifest.entrypoint

        potential_paths = []

        if variant:
            potential_paths.append(
                f"{component_path_base}/skins/{variant}/{entrypoint_filename}"
            )

        potential_paths.append(f"{component_path_base}/{entrypoint_filename}")

        if entrypoint_filename == "main.html":
            potential_paths.append(f"{component_path_base}.html")

        for path in potential_paths:
            try:
                self.jinja_env.get_template(path)
                logger.debug(f"解析到模板路径: '{path}'")
                context.resolved_template_paths[cache_key] = path
                return path
            except TemplateNotFound:
                continue

        err_msg = (
            f"无法为组件 '{component_path_base}' 找到任何可用的模板。"
            f"检查路径: {potential_paths}"
        )
        logger.error(err_msg)
        raise TemplateNotFound(err_msg)

    async def get_template_manifest(
        self, component_path: str
    ) -> TemplateManifest | None:
        """
        查找并解析组件的 manifest.json 文件。
        """
        manifest_path_str = f"{component_path}/manifest.json"

        if not self.jinja_env.loader:
            return None

        try:
            _, full_path, _ = self.jinja_env.loader.get_source(
                self.jinja_env, manifest_path_str
            )
            if full_path and Path(full_path).exists():
                async with aiofiles.open(full_path, encoding="utf-8") as f:
                    manifest_data = json.loads(await f.read())
                return TemplateManifest(**manifest_data)
        except TemplateNotFound:
            return None
        return None

    async def resolve_markdown_style_path(
        self, style_name: str, context: "RenderContext"
    ) -> Path | None:
        """
        按照 注册 -> 主题约定 -> 默认约定 的顺序解析 Markdown 样式路径。
        [新逻辑] 使用传入的上下文进行缓存。
        """
        if cached_path := context.resolved_style_paths.get(style_name):
            logger.trace(f"Markdown样式路径缓存命中: '{style_name}'")
            return cached_path

        resolved_path: Path | None = None
        if registered_path := asset_registry.resolve_markdown_style(style_name):
            logger.debug(f"找到已注册的 Markdown 样式: '{style_name}'")
            resolved_path = registered_path

        elif self.current_theme:
            theme_style_path = (
                self.current_theme.assets_dir
                / "css"
                / "styles"
                / "markdown"
                / f"{style_name}.css"
            )
            if theme_style_path.exists():
                logger.debug(
                    f"在主题 '{self.current_theme.name}' 中找到"
                    f"Markdown 样式: '{style_name}'"
                )
                resolved_path = theme_style_path

            default_style_path = (
                self.current_theme.default_assets_dir
                / "css"
                / "styles"
                / "markdown"
                / f"{style_name}.css"
            )
            if not resolved_path and default_style_path.exists():
                logger.debug(f"在 'default' 主题中找到 Markdown 样式: '{style_name}'")
                resolved_path = default_style_path

        if resolved_path:
            context.resolved_style_paths[style_name] = resolved_path
        else:
            logger.warning(
                f"Markdown 样式 '{style_name}' 在注册表和主题目录中均未找到。"
            )

        return resolved_path

    async def _render_component_to_html(
        self,
        context: "RenderContext",
        **kwargs,
    ) -> str:
        """将 Renderable 组件渲染成 HTML 字符串，并处理异步数据。"""
        component = context.component
        assert self.current_theme is not None, "主题加载失败"

        data_dict = component.get_render_data()

        theme_context_dict = model_dump(self.current_theme)

        theme_css_template = self.jinja_env.get_template("theme.css.jinja")
        theme_css_content = await theme_css_template.render_async(
            theme=theme_context_dict
        )

        resolved_template_name = await self._resolve_component_template(
            component, context
        )
        logger.debug(
            f"正在渲染组件 '{component.template_name}' "
            f"(主题: {self.current_theme.name})，解析模板: '{resolved_template_name}'",
            "渲染服务",
        )
        template = self.jinja_env.get_template(resolved_template_name)

        unpacked_data = {}
        for key, value in data_dict.items():
            if key in RESERVED_TEMPLATE_KEYS:
                logger.warning(
                    f"模板数据键 '{key}' 与渲染器保留关键字冲突，"
                    f"在模板 '{component.template_name}' 中请使用 'data.{key}' 访问。"
                )
            else:
                unpacked_data[key] = value

        template_context = {
            "data": component,
            "theme": theme_context_dict,
            "frameless": kwargs.get("frameless", False),
        }
        template_context.update(unpacked_data)
        template_context.update(kwargs)

        html_fragment = await template.render_async(**template_context)

        if not kwargs.get("frameless", False):
            base_template = self.jinja_env.get_template("partials/_base.html")
            page_context = {
                "data": component,
                "theme_css": theme_css_content,
                "collected_inline_css": context.collected_inline_css,
                "required_scripts": list(context.collected_scripts),
                "collected_asset_styles": list(context.collected_asset_styles),
                "body_content": html_fragment,
            }
            return await base_template.render_async(**page_context)
        else:
            return html_fragment
