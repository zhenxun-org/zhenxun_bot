"""
LLM 模型管理器
对外提供统一的配置查询、模型发现与实例化入口。
"""

from typing import Any

from zhenxun.services.ai.config import (
    ProviderConfig,
    get_ai_config,
    get_llm_config,
)
from zhenxun.services.ai.core.exceptions import ConfigurationException
from zhenxun.services.ai.core.models import ModelDetail
from zhenxun.services.ai.core.options import GenerationConfig
from zhenxun.services.ai.utils.logger import log_llm as logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.pydantic_compat import model_dump

from .system.cache import clear_model_cache, get_or_create_model
from .system.capabilities import get_model_capabilities
from .system.network import health_manager

_RESOLVED_GROUP_CACHE: dict[str, list[str]] = {}
"""路由组解析缓存，避免每次调用重复打印剔除警告并提升性能"""


def clear_resolved_group_cache() -> None:
    global _RESOLVED_GROUP_CACHE
    _RESOLVED_GROUP_CACHE.clear()


def parse_provider_model_string(name_str: str | None) -> tuple[str | None, str | None]:
    """解析 'ProviderName/ModelName' 格式的字符串"""
    if not name_str or "/" not in name_str:
        return None, None
    parts = name_str.split("/", 1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return None, None


def _get_group_name(name_str: str) -> str | None:
    """判断名称是否是组名，如果是则提取并返回组名，否则返回 None"""
    name_str = name_str.strip()
    if "/" not in name_str:
        return name_str
    return None


def get_default_api_base_for_type(api_type: str) -> str | None:
    """根据API类型获取默认的API基础地址"""
    default_api_bases = {
        "openai": "https://api.openai.com",
        "doubao": "https://ark.cn-beijing.volces.com/api",
        "deepseek": "https://api.deepseek.com",
        "jina": "https://api.jina.ai",
        "glm": "https://open.bigmodel.cn",
        "gemini": "https://generativelanguage.googleapis.com",
        "openrouter": "https://openrouter.ai/api",
        "smart": None,
        "openai_responses": None,
    }
    return default_api_bases.get(api_type)


def get_configured_providers() -> list[ProviderConfig]:
    """从配置中获取Provider列表"""
    ai_config = get_ai_config()
    providers = ai_config.get("PROVIDERS", [])
    if not isinstance(providers, list):
        logger.error("配置项 AI.PROVIDERS 的值不是一个列表，将使用空列表。")
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
            )
    return valid_providers


def find_model_config(
    provider_name: str, model_name: str
) -> tuple[ProviderConfig, ModelDetail] | None:
    """在配置中查找指定 Provider 与 ModelDetail。"""
    providers = get_configured_providers()
    for provider in providers:
        if provider.name.lower() == provider_name.lower():
            for model_detail in provider.models:
                if model_detail.model_name.lower() == model_name.lower():
                    return provider, model_detail
    return None


def _resolve_model_group(group_name: str, visited: set | None = None) -> list[str]:
    """递归解析模型组，展开为扁平的真实模型列表，并防止循环嵌套。"""
    global _RESOLVED_GROUP_CACHE
    if visited is None and group_name in _RESOLVED_GROUP_CACHE:
        return _RESOLVED_GROUP_CACHE[group_name]
    if visited is None:
        visited = set()
    if group_name in visited:
        logger.warning(f"检测到模型路由组嵌套死循环: {group_name}，已安全跳过该分支。")
        return []
    visited.add(group_name)
    llm_config = get_llm_config()
    if group_name not in llm_config.model_groups:
        logger.warning(f"模型路由组 '{group_name}' 不存在于配置中。")
        return []
    resolved_models = []
    for item in llm_config.model_groups[group_name]:
        item = item.strip()
        sub_group = _get_group_name(item)
        if sub_group:
            resolved_models.extend(_resolve_model_group(sub_group, visited.copy()))
        else:
            prov_mod = parse_provider_model_string(item)
            if prov_mod[0] and prov_mod[1]:
                if find_model_config(prov_mod[0], prov_mod[1]):
                    if item not in resolved_models:
                        resolved_models.append(item)
                else:
                    logger.warning(
                        f"⚠️ [Router] 路由组 '{group_name}' 中的模型 "
                        f"'{item}' 未在配置，已被自动剔除！"
                    )
            else:
                logger.warning(f"路由组 '{group_name}' 包含无效格式的项目 '{item}'。")
    if len(visited) == 1:
        _RESOLVED_GROUP_CACHE[group_name] = resolved_models
    return resolved_models


def _get_model_identifiers(provider_name: str, model_detail: ModelDetail) -> list[str]:
    """获取模型的所有可用标识符"""
    return [f"{provider_name}/{model_detail.model_name}"]


def list_available_models() -> list[dict[str, Any]]:
    """列出所有已配置的可用模型及其信息。"""
    providers = get_configured_providers()
    model_list = []
    for provider in providers:
        for model_detail in provider.models:
            caps = get_model_capabilities(model_detail.model_name)
            model_info = {
                "provider_name": provider.name,
                "model_name": model_detail.model_name,
                "full_name": f"{provider.name}/{model_detail.model_name}",
                "api_type": provider.api_type or "auto-detect",
                "api_base": provider.api_base,
                "is_available": model_detail.is_available,
                "is_embedding_model": caps.is_embedding_model,
                "max_input_tokens": caps.max_input_tokens,
                "available_identifiers": _get_model_identifiers(
                    provider.name, model_detail
                ),
            }
            model_list.append(model_info)
    return model_list


def list_embedding_models() -> list[dict[str, Any]]:
    """列出所有支持嵌入能力的模型。"""
    all_models = list_available_models()
    return [model for model in all_models if model.get("is_embedding_model", False)]


def list_model_identifiers() -> dict[str, list[str]]:
    """列出所有模型的可用标识符映射。"""
    providers = get_configured_providers()
    result = {}
    for provider in providers:
        for model_detail in provider.models:
            full_name = f"{provider.name}/{model_detail.model_name}"
            identifiers = _get_model_identifiers(provider.name, model_detail)
            result[full_name] = identifiers
    return result


def get_default_model(task: str = "chat") -> str | None:
    """根据任务类型获取默认模型名称"""
    config = get_llm_config()
    return getattr(config.default_models, task, None)


async def get_key_usage_stats() -> dict[str, Any]:
    """获取所有 Provider 的 Key 使用统计。"""
    providers = get_configured_providers()
    stats = {}
    for provider in providers:
        keys = (
            [provider.api_key]
            if isinstance(provider.api_key, str)
            else provider.api_key
        )
        provider_stats = {}
        provider_state = health_manager.state.providers.get(provider.name)
        if provider_state:
            for k in keys:
                stat_data = provider_state.api_keys.get(k)
                if stat_data:
                    provider_stats[health_manager._get_key_id(k)] = model_dump(
                        stat_data
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
    """重置指定 Provider 的 Key 状态。"""
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
            await health_manager.reset_key_status(target_provider.name, api_key)
            logger.info(f"已重置Provider '{provider_name}' 的指定Key状态")
            return True
        else:
            logger.error(f"指定的Key不属于Provider '{provider_name}'")
            return False
    else:
        for key in provider_keys:
            await health_manager.reset_key_status(target_provider.name, key)
        logger.info(f"已重置Provider '{provider_name}' 的所有Key状态")
        return True


async def get_model_instance(
    provider_model_name: str | None = None,
    override_config: dict[str, Any] | GenerationConfig | None = None,
    task: str = "chat",
) -> Any:
    """作为门面 API，解析字符串并调用底层的 get_or_create_model"""
    resolved_model_name_str = provider_model_name
    if resolved_model_name_str is None:
        resolved_model_name_str = get_default_model(task)
        if resolved_model_name_str is None:
            available_models_list = list_available_models()
            if not available_models_list:
                raise ConfigurationException("未配置任何AI模型")
            resolved_model_name_str = available_models_list[0]["full_name"]
            logger.warning(f"未指定模型，使用第一个可用模型: {resolved_model_name_str}")

    prov_name_str, mod_name_str = parse_provider_model_string(resolved_model_name_str)
    if not prov_name_str or not mod_name_str:
        raise ConfigurationException(f"无效的模型名称格式: '{resolved_model_name_str}'")

    config_tuple_found = find_model_config(prov_name_str, mod_name_str)
    if not config_tuple_found:
        raise ConfigurationException(f"未找到模型: '{resolved_model_name_str}'. ")

    provider_config_found, model_detail_found = config_tuple_found

    return await get_or_create_model(
        provider_config_found, model_detail_found, override_config
    )


def clear_all_cache() -> None:
    """
    清空模型实例缓存与路由组解析缓存。
    """
    clear_model_cache()
    clear_resolved_group_cache()
    logger.debug("已清空全局模型实例与路由组缓存")


@PriorityLifecycle.on_startup(priority=10)
async def _init_llm_config_on_startup():
    """启动时初始化 LLM 配置、密钥状态并预热工具提供者管理器。"""
    logger.info("正在初始化 LLM 配置并加载遥测状态...")
    try:
        from zhenxun.services.ai.tools.engine.registry import tool_provider_manager

        get_llm_config()
        await health_manager.initialize()
        await tool_provider_manager.initialize()

    except Exception as e:
        logger.error(f"LLM 配置或遥测状态初始化时发生错误: {e}", e=e)
