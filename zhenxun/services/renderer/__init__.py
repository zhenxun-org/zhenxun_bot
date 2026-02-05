from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from .service import RendererService
from .types import Renderable, RenderResult

renderer_service = RendererService()


@PriorityLifecycle.on_startup(priority=10)
async def _init_renderer_service():
    """在Bot启动时初始化渲染服务及其依赖。"""
    await renderer_service.initialize()


__all__ = ["RenderResult", "Renderable", "renderer_service"]
