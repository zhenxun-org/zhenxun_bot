"""
LLM 模型管理器

负责模型实例的创建、缓存、配置管理和生命周期管理。
"""

import hashlib
import json
import time
from typing import Any

from zhenxun.configs.config import Config
from zhenxun.services.log import logger

from .config import validate_override_params
from .config.providers import AI_CONFIG_GROUP, PROVIDERS_CONFIG_KEY, get_ai_config
from .core import http_client_manager, key_store
from .service import LLMModel
from .types import LLMErrorCode, LLMException, ModelDetail, ProviderConfig
from .types.capabilities import get_model_capabilities

DEFAULT_MODEL_NAME_KEY = "default_model_name"
PROXY_KEY = "proxy"
TIMEOUT_KEY = "timeout"

_model_cache: dict[str, tuple[LLMModel, float]] = {}
_cache_ttl = 3600
_max_cache_size = 10


def parse_provider_model_string(name_str: str | None) -> tuple[str | None, str | None]:
    """解析 'ProviderName/ModelName' 格式的字符串"""
    if not name_str or "/" not in name_str:
        return None, None
    parts = name_str.split("/", 1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return None, None


def _make_cache_key(
    provider_model_name: str | None, override_config: dict | None
) -> str:
    """生成缓存键"""
    config_str = (
        json.dumps(override_config, sort_keys=True) if override_config else "None"
    )
    key_data = f"{provider_model_name}:{config_str}"
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
    """清空模型缓存"""
    global _model_cache
    _model_cache.clear()
    logger.info("已清空模型缓存")


def get_cache_stats() -> dict[str, Any]:
    """获取缓存统计信息"""
    return {
        "cache_size": len(_model_cache),
        "max_cache_size": _max_cache_size,
        "cache_ttl": _cache_ttl,
        "cached_models": list(_model_cache.keys()),
    }


def get_default_api_base_for_type(api_type: str) -> str | None:
    """根据API类型获取默认的API基础地址"""
    default_api_bases = {
        "openai": "https://api.openai.com",
        "deepseek": "https://api.deepseek.com",
        "zhipu": "https://open.bigmodel.cn",
        "gemini": "https://generativelanguage.googleapis.com",
        "general_openai_compat": None,
    }

    return default_api_bases.get(api_type)


def get_configured_providers() -> list[ProviderConfig]:
    """从配置中获取Provider列表 - 简化和修正版本"""
    ai_config = get_ai_config()
    providers = ai_config.get(PROVIDERS_CONFIG_KEY, [])

    if not isinstance(providers, list):
        logger.error(
            f"配置项 {AI_CONFIG_GROUP}.{PROVIDERS_CONFIG_KEY} 的值不是一个列表，"
            f"将使用空列表。"
        )
        return []

    valid_providers = []
    for i, item in enumerate(providers):
        if isinstance(item, ProviderConfig):
            if not item.api_base:
                default_api_base = get_default_api_base_for_type(item.api_type)
                if default_api_base:
                    item.api_base = default_api_base
            valid_providers.append(item)
        else:
            logger.warning(
                f"配置文件中第 {i + 1} 项未能正确解析为 ProviderConfig 对象，已跳过。"
                f"实际类型: {type(item)}"
            )

    return valid_providers


def find_model_config(
    provider_name: str, model_name: str
) -> tuple[ProviderConfig, ModelDetail] | None:
    """
    在配置中查找指定的 Provider 和 ModelDetail

    参数:
        provider_name: 提供商名称
        model_name: 模型名称

    返回:
        tuple[ProviderConfig, ModelDetail] | None: 找到的配置元组，未找到则返回 None
    """
    providers = get_configured_providers()

    for provider in providers:
        if provider.name.lower() == provider_name.lower():
            for model_detail in provider.models:
                if model_detail.model_name.lower() == model_name.lower():
                    return provider, model_detail

    return None


def list_available_models() -> list[dict[str, Any]]:
    """列出所有配置的可用模型"""
    providers = get_configured_providers()
    model_list = []
    for provider in providers:
        for model_detail in provider.models:
            model_info = {
                "provider_name": provider.name,
                "model_name": model_detail.model_name,
                "full_name": f"{provider.name}/{model_detail.model_name}",
                "api_type": provider.api_type or "auto-detect",
                "api_base": provider.api_base,
                "is_available": model_detail.is_available,
                "is_embedding_model": model_detail.is_embedding_model,
                "available_identifiers": _get_model_identifiers(
                    provider.name, model_detail
                ),
            }
            model_list.append(model_info)
    return model_list


def _get_model_identifiers(provider_name: str, model_detail: ModelDetail) -> list[str]:
    """获取模型的所有可用标识符"""
    return [f"{provider_name}/{model_detail.model_name}"]


def list_model_identifiers() -> dict[str, list[str]]:
    """
    列出所有模型的可用标识符

    返回:
        dict[str, list[str]]: 字典，键为模型的完整名称，值为该模型的所有可用标识符列表
    """
    providers = get_configured_providers()
    result = {}

    for provider in providers:
        for model_detail in provider.models:
            full_name = f"{provider.name}/{model_detail.model_name}"
            identifiers = _get_model_identifiers(provider.name, model_detail)
            result[full_name] = identifiers

    return result


def list_embedding_models() -> list[dict[str, Any]]:
    """列出所有配置的嵌入模型"""
    all_models = list_available_models()
    return [model for model in all_models if model.get("is_embedding_model", False)]


async def get_model_instance(
    provider_model_name: str | None = None,
    override_config: dict[str, Any] | None = None,
) -> LLMModel:
    """
    根据 'ProviderName/ModelName' 字符串获取并实例化 LLMModel (异步版本)

    参数:
        provider_model_name: 模型名称，格式为 'ProviderName/ModelName'。
        override_config: 覆盖配置字典。

    返回:
        LLMModel: 模型实例。
    """
    cache_key = _make_cache_key(provider_model_name, override_config)
    cached_model = _get_cached_model(cache_key)
    if cached_model:
        if override_config:
            validated_override = validate_override_params(override_config)
            if cached_model._generation_config != validated_override:
                cached_model._generation_config = validated_override
                logger.debug(
                    f"对缓存模型 {provider_model_name} 应用新的覆盖配置: "
                    f"{validated_override.to_dict()}"
                )
        return cached_model

    resolved_model_name_str = provider_model_name
    if resolved_model_name_str is None:
        resolved_model_name_str = get_global_default_model_name()
        if resolved_model_name_str is None:
            available_models_list = list_available_models()
            if not available_models_list:
                raise LLMException(
                    "未配置任何AI模型", code=LLMErrorCode.CONFIGURATION_ERROR
                )
            resolved_model_name_str = available_models_list[0]["full_name"]
            logger.warning(f"未指定模型，使用第一个可用模型: {resolved_model_name_str}")

    prov_name_str, mod_name_str = parse_provider_model_string(resolved_model_name_str)
    if not prov_name_str or not mod_name_str:
        raise LLMException(
            f"无效的模型名称格式: '{resolved_model_name_str}'",
            code=LLMErrorCode.MODEL_NOT_FOUND,
        )

    config_tuple_found = find_model_config(prov_name_str, mod_name_str)
    if not config_tuple_found:
        all_models = list_available_models()
        raise LLMException(
            f"未找到模型: '{resolved_model_name_str}'. "
            f"可用: {[m['full_name'] for m in all_models]}",
            code=LLMErrorCode.MODEL_NOT_FOUND,
        )

    provider_config_found, model_detail_found = config_tuple_found

    capabilities = get_model_capabilities(model_detail_found.model_name)

    model_detail_found.is_embedding_model = capabilities.is_embedding_model

    ai_config = get_ai_config()
    global_proxy_setting = ai_config.get(PROXY_KEY)
    default_timeout = (
        provider_config_found.timeout
        if provider_config_found.timeout is not None
        else 180
    )
    global_timeout_setting = ai_config.get(TIMEOUT_KEY, default_timeout)

    config_for_http_client = ProviderConfig(
        name=provider_config_found.name,
        api_key=provider_config_found.api_key,
        models=provider_config_found.models,
        timeout=global_timeout_setting,
        proxy=global_proxy_setting,
        api_base=provider_config_found.api_base,
        api_type=provider_config_found.api_type,
        openai_compat=provider_config_found.openai_compat,
        temperature=provider_config_found.temperature,
        max_tokens=provider_config_found.max_tokens,
    )

    shared_http_client = await http_client_manager.get_client(config_for_http_client)

    try:
        model_instance = LLMModel(
            provider_config=config_for_http_client,
            model_detail=model_detail_found,
            key_store=key_store,
            http_client=shared_http_client,
            capabilities=capabilities,
        )

        if override_config:
            validated_override_params = validate_override_params(override_config)
            model_instance._generation_config = validated_override_params
            logger.debug(
                f"为新模型 {resolved_model_name_str} 应用配置覆盖: "
                f"{validated_override_params.to_dict()}"
            )

        _cache_model(cache_key, model_instance)
        logger.debug(
            f"创建并缓存了新模型: {cache_key} -> {prov_name_str}/{mod_name_str}"
        )
        return model_instance
    except LLMException:
        raise
    except Exception as e:
        logger.error(
            f"实例化 LLMModel ({resolved_model_name_str}) 时发生内部错误: {e!s}", e=e
        )
        raise LLMException(
            f"初始化模型 '{resolved_model_name_str}' 失败: {e!s}",
            code=LLMErrorCode.MODEL_INIT_FAILED,
            cause=e,
        )


def get_global_default_model_name() -> str | None:
    """获取全局默认模型名称"""
    ai_config = get_ai_config()
    return ai_config.get(DEFAULT_MODEL_NAME_KEY)


def set_global_default_model_name(provider_model_name: str | None) -> bool:
    """
    设置全局默认模型名称

    参数:
        provider_model_name: 模型名称，格式为 'ProviderName/ModelName'。

    返回:
        bool: 设置是否成功。
    """
    if provider_model_name:
        prov_name, mod_name = parse_provider_model_string(provider_model_name)
        if not prov_name or not mod_name or not find_model_config(prov_name, mod_name):
            logger.error(
                f"尝试设置的全局默认模型 '{provider_model_name}' 无效或未配置。"
            )
            return False

    Config.set_config(
        AI_CONFIG_GROUP, DEFAULT_MODEL_NAME_KEY, provider_model_name, auto_save=True
    )
    if provider_model_name:
        logger.info(f"LLM 服务全局默认模型已更新为: {provider_model_name}")
    else:
        logger.info("LLM 服务全局默认模型已清除。")
    return True


async def get_key_usage_stats() -> dict[str, Any]:
    """
    获取所有Provider的Key使用统计

    返回:
        dict[str, Any]: 包含所有Provider的Key使用统计信息。
    """
    providers = get_configured_providers()
    stats = {}

    for provider in providers:
        provider_stats = await key_store.get_key_stats(
            [provider.api_key]
            if isinstance(provider.api_key, str)
            else provider.api_key
        )
        stats[provider.name] = {
            "total_keys": len(
                [provider.api_key]
                if isinstance(provider.api_key, str)
                else provider.api_key
            ),
            "key_stats": provider_stats,
        }

    return stats


async def reset_key_status(provider_name: str, api_key: str | None = None) -> bool:
    """
    重置指定Provider的Key状态

    参数:
        provider_name: 提供商名称。
        api_key: 要重置的特定API密钥，如果为None则重置所有密钥。

    返回:
        bool: 重置是否成功。
    """
    providers = get_configured_providers()
    target_provider = None

    for provider in providers:
        if provider.name.lower() == provider_name.lower():
            target_provider = provider
            break

    if not target_provider:
        logger.error(f"未找到Provider: {provider_name}")
        return False

    provider_keys = (
        [target_provider.api_key]
        if isinstance(target_provider.api_key, str)
        else target_provider.api_key
    )

    if api_key:
        if api_key in provider_keys:
            await key_store.reset_key_status(api_key)
            logger.info(f"已重置Provider '{provider_name}' 的指定Key状态")
            return True
        else:
            logger.error(f"指定的Key不属于Provider '{provider_name}'")
            return False
    else:
        for key in provider_keys:
            await key_store.reset_key_status(key)
        logger.info(f"已重置Provider '{provider_name}' 的所有Key状态")
        return True
