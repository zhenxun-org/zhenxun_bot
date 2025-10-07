from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
from zhenxun.services.renderer.protocols import Renderable
from zhenxun.services.renderer.registry import asset_registry
from zhenxun.utils.pydantic_compat import model_dump

if TYPE_CHECKING:
    from .service import RenderContext

from .config import RESERVED_TEMPLATE_KEYS


def deep_merge_dict(base: dict, new: dict) -> dict:
    """
    递归地将 new 字典合并到 base 字典中。
    new 字典中的值会覆盖 base 字典中的值。
    """
    result = base.copy()
    for key, value in new.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


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


class ResourceResolver:
    """
    一个独立的、用于解析组件和主题资源的类。
    封装了所有复杂的路径查找和回退逻辑。

    资源解析遵循以下回退顺序，以支持强大的主题覆盖和组件化:

    1.  **相对路径 (`./`)**: 对于在模板中使用 `asset('./style.css')` 的情况，
        这是组件内部的资源。
        a.  **皮肤资源**: 首先在当前组件的皮肤目录中查找
            (`.../skins/{variant_name}/assets/`)。
            这允许皮肤完全覆盖其组件的默认资源。
        b.  **当前主题组件资源**: 接着在当前激活主题的组件根目录中查找
            (`.../{theme_name}/.../assets/`)。
        c.  **默认主题组件资源**: 如果仍未找到，最后回退到 `default` 主题中
            对应的组件目录
            (`.../default/.../assets/`) 查找。这是核心的回退逻辑。

    2.  **全局路径**: 对于使用 `asset('js/script.js')` 的情况，这是主题的全局资源。
        a.  **当前主题全局资源**: 在当前激活主题的根 `assets` 目录中查找
            (`themes/{theme_name}/assets/`)。
        b.  **默认主题全局资源**: 如果找不到，则回退到 `default` 主题的根 `assets` 目录
            (`themes/default/assets/`)。
    """

    def __init__(self, theme_manager: "ThemeManager"):
        self.theme_manager = theme_manager

    def _find_component_root(self, start_path: Path) -> Path:
        """从给定路径向上查找，找到包含 manifest.json 的组件根目录。"""
        current_path = start_path.parent
        themes_root_parts = len(THEMES_PATH.parts)
        for _ in range(len(current_path.parts) - themes_root_parts):
            if (current_path / "manifest.json").exists():
                return current_path
            if current_path.parent == current_path:
                break
            current_path = current_path.parent
        return start_path.parent

    def _search_paths_for_relative_asset(
        self, asset_path: str, parent_template_name: str
    ) -> list[tuple[str, Path]]:
        """为相对路径的资源生成所有可能的查找路径元组 (描述, 路径)。"""
        if not self.theme_manager.current_theme:
            return []

        paths_to_check: list[tuple[str, Path]] = []
        current_theme_name = self.theme_manager.current_theme.name
        current_theme_root = self.theme_manager.current_theme.assets_dir.parent
        default_theme_root = self.theme_manager.current_theme.default_assets_dir.parent

        if not self.theme_manager.jinja_env.loader:
            return []

        source_info = self.theme_manager.jinja_env.loader.get_source(
            self.theme_manager.jinja_env, parent_template_name
        )
        if not source_info[1]:
            return []

        parent_template_abs_path = Path(source_info[1])

        component_logical_root = Path(parent_template_name).parent

        if (
            "/skins/" in parent_template_abs_path.as_posix()
            or "\\skins\\" in parent_template_abs_path.as_posix()
        ):
            skin_dir = parent_template_abs_path.parent
            paths_to_check.append(
                (
                    f"'{current_theme_name}' 主题皮肤资源",
                    skin_dir / "assets" / asset_path,
                )
            )

        paths_to_check.append(
            (
                f"'{current_theme_name}' 主题组件资源",
                current_theme_root / component_logical_root / "assets" / asset_path,
            )
        )

        if current_theme_name != "default":
            paths_to_check.append(
                (
                    "'default' 主题组件资源 (回退)",
                    default_theme_root / component_logical_root / "assets" / asset_path,
                )
            )
        return paths_to_check

    def resolve_asset_uri(self, asset_path: str, current_template_name: str) -> str:
        """解析资源路径，实现完整的回退逻辑，并返回可用的URI。"""
        if (
            not self.theme_manager.current_theme
            or not self.theme_manager.jinja_env.loader
        ):
            return ""

        if asset_path.startswith("@"):
            try:
                full_asset_path = self.theme_manager.jinja_env.join_path(
                    asset_path, current_template_name
                )
                _source, file_abs_path, _uptodate = (
                    self.theme_manager.jinja_env.loader.get_source(
                        self.theme_manager.jinja_env, full_asset_path
                    )
                )
                if file_abs_path:
                    logger.debug(
                        f"Jinja Loader resolved asset '{asset_path}'->'{file_abs_path}'"
                    )
                    return Path(file_abs_path).absolute().as_uri()
            except TemplateNotFound:
                logger.warning(
                    f"资源文件在命名空间中未找到: '{asset_path}'"
                    f"(在模板 '{current_template_name}' 中引用)"
                )
                return ""

        search_paths: list[tuple[str, Path]] = []
        if asset_path.startswith("./") or asset_path.startswith("../"):
            relative_part = (
                asset_path[2:] if asset_path.startswith("./") else asset_path
            )
            search_paths.extend(
                self._search_paths_for_relative_asset(
                    relative_part, current_template_name
                )
            )
        else:
            search_paths.append(
                (
                    f"'{self.theme_manager.current_theme.name}' 主题全局资源",
                    self.theme_manager.current_theme.assets_dir / asset_path,
                )
            )
            if self.theme_manager.current_theme.name != "default":
                search_paths.append(
                    (
                        "'default' 主题全局资源 (回退)",
                        self.theme_manager.current_theme.default_assets_dir
                        / asset_path,
                    )
                )

        for source_desc, path in search_paths:
            if path.exists():
                logger.debug(f"解析资源 '{asset_path}' -> 找到 {source_desc}: '{path}'")
                return path.absolute().as_uri()

        logger.warning(
            f"资源文件未找到: '{asset_path}' (在模板 '{current_template_name}' 中引用)"
        )
        return ""


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

        self._manifest_cache: dict[str, Any] = {}
        self._manifest_cache_lock = asyncio.Lock()

    def list_available_themes(self) -> list[str]:
        """扫描主题目录并返回所有可用的主题名称。"""
        if not THEMES_PATH.is_dir():
            return []
        return [d.name for d in THEMES_PATH.iterdir() if d.is_dir()]

    def _create_asset_loader(self) -> Callable[..., str]:
        """
        创建一个闭包函数 (Jinja2中的 `asset()` 函数)，使用
        ResourceResolver 进行路径解析。
        """
        resolver = ResourceResolver(self)

        @pass_context
        def asset_loader(ctx, asset_path: str) -> str:
            if not ctx.name:
                logger.warning("Jinja2 上下文缺少模板名称，无法进行资源解析。")
                return resolver.resolve_asset_uri(asset_path, "unknown_template")
            parent_template_name = ctx.name
            return resolver.resolve_asset_uri(asset_path, parent_template_name)

        return asset_loader

    def _create_standalone_asset_loader(
        self, local_base_path: Path
    ) -> Callable[[str], str]:
        """为独立模板创建一个专用的 asset loader。"""
        resolver = ResourceResolver(self)

        def asset_loader(asset_path: str) -> str:
            return resolver.resolve_asset_uri(asset_path, str(local_base_path))

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
                self.jinja_env.loader.loaders = [prefix_loader, new_theme_loader]

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

        base_manifest = await self.get_template_manifest(component_path_base)

        skin_to_use = variant or (base_manifest.get("skin") if base_manifest else None)

        final_manifest = await self.get_template_manifest(
            component_path_base, skin=skin_to_use
        )
        logger.debug(f"final_manifest: {final_manifest}")

        entrypoint_filename = (
            final_manifest.get("entrypoint", "main.html")
            if final_manifest
            else "main.html"
        )

        potential_paths = []

        if skin_to_use:
            potential_paths.append(
                f"{component_path_base}/skins/{skin_to_use}/{entrypoint_filename}"
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

    async def _load_single_manifest(self, path_str: str) -> dict[str, Any] | None:
        """从指定路径加载单个 manifest.json 文件。"""
        normalized_path = path_str.replace("\\", "/")
        manifest_path_str = f"{normalized_path}/manifest.json"

        if not self.jinja_env.loader:
            return None

        try:
            source, filepath, _ = self.jinja_env.loader.get_source(
                self.jinja_env, manifest_path_str
            )
            logger.debug(f"找到清单文件: '{manifest_path_str}' (从 '{filepath}' 加载)")
            return json.loads(source)
        except TemplateNotFound:
            logger.trace(f"未找到清单文件: '{manifest_path_str}'")
            return None
        except json.JSONDecodeError:
            logger.warning(f"清单文件 '{manifest_path_str}' 解析失败")
            return None

    async def _load_and_merge_manifests(
        self, component_path: Path | str, skin: str | None = None
    ) -> dict[str, Any] | None:
        """加载基础和皮肤清单并进行合并。"""
        logger.debug(f"开始加载清单: component_path='{component_path}', skin='{skin}'")

        base_manifest = await self._load_single_manifest(str(component_path))

        if skin:
            skin_path = Path(component_path) / "skins" / skin
            skin_manifest = await self._load_single_manifest(str(skin_path))

            if skin_manifest:
                if base_manifest:
                    merged = deep_merge_dict(base_manifest, skin_manifest)
                    logger.debug(
                        f"已合并基础清单和皮肤清单: '{component_path}' + skin '{skin}'"
                    )
                    return merged
                else:
                    logger.debug(f"只找到皮肤清单: '{skin_path}'")
                    return skin_manifest

        if base_manifest:
            logger.debug(f"只找到基础清单: '{component_path}'")
        else:
            logger.debug(f"未找到任何清单: '{component_path}'")

        return base_manifest

    async def get_template_manifest(
        self, component_path: str, skin: str | None = None
    ) -> dict[str, Any] | None:
        """
        查找并解析组件的 manifest.json 文件。
        支持皮肤清单的继承与合并,并带有缓存。

        Args:
            component_path: 组件路径
            skin: 皮肤名称(可选)

        Returns:
            合并后的清单字典,如果不存在则返回 None
        """
        cache_key = f"{component_path}:{skin or 'base'}"

        if cache_key in self._manifest_cache:
            logger.debug(f"清单缓存命中: '{cache_key}'")
            return self._manifest_cache[cache_key]

        async with self._manifest_cache_lock:
            if cache_key in self._manifest_cache:
                logger.debug(f"清单缓存命中(锁内): '{cache_key}'")
                return self._manifest_cache[cache_key]

            manifest = await self._load_and_merge_manifests(component_path, skin)

            self._manifest_cache[cache_key] = manifest
            logger.debug(f"清单已缓存: '{cache_key}'")

            return manifest

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
