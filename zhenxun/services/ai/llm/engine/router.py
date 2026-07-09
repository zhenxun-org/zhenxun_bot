from abc import ABC, abstractmethod
from typing import Any

from zhenxun.services.ai.core.exceptions import (
    ConfigurationException,
    LLMException,
    UpstreamServerException,
)
from zhenxun.services.ai.core.options import GenerationConfig
from zhenxun.services.ai.llm.manager import (
    _get_group_name,
    _resolve_model_group,
    get_default_model,
    get_model_instance,
    list_available_models,
)
from zhenxun.services.ai.llm.system.capabilities import get_model_capabilities
from zhenxun.services.ai.llm.system.network import health_manager
from zhenxun.services.ai.utils.logger import log_llm as logger


class BaseModelRouter(ABC):
    @abstractmethod
    async def route(
        self,
        request: Any,
        model_names: list[str],
        task: str,
        override_config: GenerationConfig | dict | None,
        cancellation_token: Any | None,
    ) -> Any:
        pass


class FallbackRouter(BaseModelRouter):
    """主备故障转移路由器"""

    async def route(
        self,
        request: Any,
        model_names: list[str],
        task: str,
        override_config: GenerationConfig | dict | None,
        cancellation_token: Any | None,
    ) -> Any:
        errors = []
        all_nodes_bypassed = True

        is_routed_call = len(model_names) > 1
        request.extra["_is_routed_call"] = is_routed_call
        start_idx = request.extra.get("_working_route_index", 0)
        indices_to_try = list(range(start_idx, len(model_names))) + list(
            range(0, start_idx)
        )

        for idx in indices_to_try:
            m_name = model_names[idx]

            if not health_manager.is_route_healthy(m_name, strict_mode=is_routed_call):
                logger.debug(f"👉 节点 '{m_name}' 熔断中，已跳过")
                errors.append(f"{m_name}(熔断中)")
                continue

            caps = get_model_capabilities(m_name)
            if not caps.supports_task(task):
                errors.append(f"{m_name}(Unsupported Task: {task})")
                continue

            all_nodes_bypassed = False
            try:
                if len(model_names) > 1:
                    if idx != start_idx:
                        logger.debug(f"🔄 切换至备用节点: '{m_name}'...")

                async with await get_model_instance(
                    m_name, override_config, task=task
                ) as instance:
                    response = await instance.invoke(request, cancellation_token)
                    request.extra["_working_route_index"] = idx
                    if run_ctx := request.extra.get("run_context"):
                        run_ctx.state["_working_route_index"] = idx
                    return response
            except LLMException as e:
                if not e.should_failover:
                    logger.warning(
                        f"🚫 节点 '{m_name}' "
                        f"返回不可恢复错误 ({e.__class__.__name__})，停止故障转移。"
                    )
                    raise e
                logger.warning(
                    f"⚠️ 节点 '{m_name}' "
                    f"错误 ({e.__class__.__name__})，触发故障转移..."
                )
                errors.append(f"{m_name}({e.__class__.__name__})")
            except Exception as e:
                logger.warning(f"⚠️ 节点 '{m_name}' 发生未知异常，触发故障转移: {e}")
                errors.append(f"{m_name}(Error)")

        if all_nodes_bypassed and len(model_names) > 1:
            fallback_model = health_manager.get_best_fallback_route(model_names)
            logger.warning(
                f"⚠️ 路由组所有节点均已宕机！" f"强制放行 '{fallback_model}' 探活..."
            )
            try:
                async with await get_model_instance(
                    fallback_model, override_config, task=task
                ) as instance:
                    return await instance.invoke(request, cancellation_token)
            except Exception as e:
                errors.append(f"{fallback_model}(保底探活彻底失败:{e})")

        err_msg = f"所有路由尝试均已失败: {', '.join(errors)}"
        raise UpstreamServerException(err_msg)


class BaseOrchestrator:
    """顶层大模型请求编排器，负责组解析与路由策略委派。"""

    def __init__(self, router: BaseModelRouter | None = None):
        self.router = router or FallbackRouter()

    async def invoke(
        self,
        request: Any,
        model_name: str | None = None,
        task: str = "chat",
        override_config: GenerationConfig | dict | None = None,
        cancellation_token: Any | None = None,
    ) -> Any:
        resolved_model_name = model_name
        if resolved_model_name is None:
            resolved_model_name = get_default_model(task)
            if resolved_model_name is None:
                available_models = list_available_models()
                if not available_models:
                    raise ConfigurationException("未配置任何AI模型")
                resolved_model_name = available_models[0]["full_name"]
                logger.warning(f"未指定模型，使用第一个可用模型: {resolved_model_name}")

        group_name = _get_group_name(resolved_model_name)
        if group_name is not None:
            model_names = _resolve_model_group(group_name)
            if not model_names:
                raise ConfigurationException(
                    f"模型路由组 '{group_name}' 解析失败或为空，请检查配置。"
                )
        else:
            model_names = [resolved_model_name]

        return await self.router.route(
            request=request,
            model_names=model_names,
            task=task,
            override_config=override_config,
            cancellation_token=cancellation_token,
        )


LLMOrchestrator = BaseOrchestrator()
