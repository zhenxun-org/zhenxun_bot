import hashlib
import time
from typing import Any

from zhenxun.services.ai.config import ProviderConfig, get_llm_config
from zhenxun.services.ai.core.exceptions import ConfigurationException, LLMException
from zhenxun.services.ai.core.models import ModelDetail, ModelModality
from zhenxun.services.ai.core.options import GenerationConfig
from zhenxun.services.ai.llm.builder import validate_override_params
from zhenxun.services.ai.llm.engine.service import LLMModel
from zhenxun.services.ai.utils.logger import log_llm as logger
from zhenxun.utils.pydantic_compat import dump_json_safely, model_copy, model_dump

from .capabilities import get_model_capabilities
from .network import health_manager, http_client_manager

_model_cache: dict[str, tuple[LLMModel, float]] = {}
_cache_ttl = 3600
_max_cache_size = 10


def _make_cache_key(
    provider_name: str,
    model_name: str,
    override_config: dict | GenerationConfig | None,
) -> str:
    """生成缓存键"""
    config_str = (
        dump_json_safely(override_config, sort_keys=True) if override_config else "None"
    )
    key_data = f"{provider_name}/{model_name}:{config_str}"
    return hashlib.md5(key_data.encode()).hexdigest()


def _get_cached_model(cache_key: str) -> LLMModel | None:
    """从缓存获取模型"""
    if cache_key in _model_cache:
        model, created_time = _model_cache[cache_key]
        current_time = time.time()

        if current_time - created_time > _cache_ttl:
            del _model_cache[cache_key]
            logger.debug(f"模型缓存已过期: {cache_key}")
            return None

        if model._is_closed:
            logger.debug(
                f"缓存的模型 {cache_key} ({model.provider_name}/{model.model_name}) "
                f"处于_is_closed=True状态，重置为False以供复用。"
            )
            model._is_closed = False

        logger.debug(
            f"使用缓存的模型: {cache_key} -> {model.provider_name}/{model.model_name}"
        )
        return model
    return None


def _cache_model(cache_key: str, model: LLMModel):
    """缓存模型实例"""
    current_time = time.time()

    if len(_model_cache) >= _max_cache_size:
        oldest_key = min(_model_cache.keys(), key=lambda k: _model_cache[k][1])
        del _model_cache[oldest_key]

    _model_cache[cache_key] = (model, current_time)


def clear_model_cache():
    """仅清空内存中的模型实例缓存。"""
    global _model_cache
    _model_cache.clear()
    logger.debug("已清空模型实例缓存")


async def get_or_create_model(
    provider_config_found: ProviderConfig,
    model_detail_found: ModelDetail,
    override_config: dict[str, Any] | GenerationConfig | None = None,
) -> Any:
    """组装或获取底层 LLMModel 状态机实例 (不包含任何字符串解析)。"""
    prov_name_str = provider_config_found.name
    mod_name_str = model_detail_found.model_name

    cache_key = _make_cache_key(prov_name_str, mod_name_str, override_config)
    cached_model = _get_cached_model(cache_key)

    def _get_clean_log_config(cfg: GenerationConfig) -> dict:
        """辅助函数：剔除超长 Schema 以防止日志刷屏"""
        log_dict = model_dump(cfg, exclude_none=True)
        if "output" in log_dict and isinstance(log_dict["output"], dict):
            if "response_schema" in log_dict["output"]:
                log_dict["output"]["response_schema"] = (
                    "<JSON Schema Hidden for brevity>"
                )
        return log_dict

    if cached_model:
        if override_config:
            validated_override = validate_override_params(override_config)
            if cached_model._generation_config != validated_override:
                cached_model._generation_config = validated_override
                cached_model.identity.generation_config = validated_override
                logger.debug(
                    f"对缓存模型 {prov_name_str}/{mod_name_str} 应用新的覆盖配置: "
                    f"{_get_clean_log_config(validated_override)}"
                )
        return cached_model

    capabilities = get_model_capabilities(model_detail_found.model_name)
    capabilities = model_copy(capabilities, deep=True)

    if model_detail_found.max_input_tokens is not None:
        capabilities.max_input_tokens = model_detail_found.max_input_tokens

    base_gen_config = GenerationConfig()
    if provider_config_found.temperature is not None:
        base_gen_config.common.temperature = provider_config_found.temperature
    if provider_config_found.max_output_tokens is not None:
        base_gen_config.common.max_tokens = provider_config_found.max_output_tokens

    if model_detail_found.temperature is not None:
        base_gen_config.common.temperature = model_detail_found.temperature
    if model_detail_found.max_output_tokens is not None:
        base_gen_config.common.max_tokens = model_detail_found.max_output_tokens
    if model_detail_found.reasoning_effort is not None:
        base_gen_config.common.reasoning_effort = model_detail_found.reasoning_effort

    if model_detail_found.task_type == "image_generation":
        capabilities.output_modalities.add(ModelModality.IMAGE)
        capabilities.supports_tool_calling = False

    llm_config = get_llm_config()
    client_settings = llm_config.client_settings
    default_timeout = (
        provider_config_found.timeout
        if provider_config_found.timeout is not None
        else client_settings.timeout
    )

    config_for_http_client = ProviderConfig(
        name=provider_config_found.name,
        api_key=provider_config_found.api_key,
        models=provider_config_found.models,
        timeout=default_timeout,
        api_base=provider_config_found.api_base,
        api_type=provider_config_found.api_type,
        temperature=provider_config_found.temperature,
        max_output_tokens=provider_config_found.max_output_tokens,
    )

    shared_http_client = await http_client_manager.get_client(config_for_http_client)

    try:
        model_instance = LLMModel(
            provider_config=config_for_http_client,
            model_detail=model_detail_found,
            health_manager=health_manager,
            http_client=shared_http_client,
            capabilities=capabilities,
            config_override=base_gen_config,
        )

        if override_config:
            validated_override_params = validate_override_params(override_config)
            final_config = base_gen_config.merge_with(validated_override_params)
            model_instance._generation_config = final_config
            model_instance.identity.generation_config = final_config
            logger.debug(
                f"为新模型 {prov_name_str}/{mod_name_str} 应用配置覆盖: "
                f"{_get_clean_log_config(validated_override_params)}"
            )
        else:
            model_instance._generation_config = base_gen_config
            model_instance.identity.generation_config = base_gen_config

        _cache_model(cache_key, model_instance)
        logger.debug(
            f"创建并缓存了新模型: {cache_key} -> {prov_name_str}/{mod_name_str}"
        )
        return model_instance
    except LLMException:
        raise
    except Exception as e:
        logger.error(
            f"实例化 LLMModel ({prov_name_str}/{mod_name_str}) 时发生内部错误: {e!s}",
            e=e,
        )
        raise ConfigurationException(
            f"初始化模型 '{prov_name_str}/{mod_name_str}' 失败: {e!s}",
            cause=e,
        )
