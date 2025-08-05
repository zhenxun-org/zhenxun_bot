"""
LLM 提供商配置管理

负责注册和管理 AI 服务提供商的配置项。
"""

from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from zhenxun.configs.config import Config
from zhenxun.configs.utils import parse_as
from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from ..core import key_store
from ..tools import tool_provider_manager
from ..types.models import ModelDetail, ProviderConfig

AI_CONFIG_GROUP = "AI"
PROVIDERS_CONFIG_KEY = "PROVIDERS"


class LLMConfig(BaseModel):
    """LLM 服务配置类"""

    default_model_name: str | None = Field(
        default=None,
        description="LLM服务全局默认使用的模型名称 (格式: ProviderName/ModelName)",
    )
    proxy: str | None = Field(
        default=None,
        description="LLM服务请求使用的网络代理，例如 http://127.0.0.1:7890",
    )
    timeout: int = Field(default=180, description="LLM服务API请求超时时间（秒）")
    max_retries_llm: int = Field(
        default=3, description="LLM服务请求失败时的最大重试次数"
    )
    retry_delay_llm: int = Field(
        default=2, description="LLM服务请求重试的基础延迟时间（秒）"
    )
    providers: list[ProviderConfig] = Field(
        default_factory=list, description="配置多个 AI 服务提供商及其模型信息"
    )

    def get_provider_by_name(self, name: str) -> ProviderConfig | None:
        """根据名称获取提供商配置

        参数:
            name: 提供商名称

        返回:
            ProviderConfig | None: 提供商配置，如果未找到则返回 None
        """
        for provider in self.providers:
            if provider.name == name:
                return provider
        return None

    def get_model_by_provider_and_name(
        self, provider_name: str, model_name: str
    ) -> tuple[ProviderConfig, ModelDetail] | None:
        """根据提供商名称和模型名称获取配置

        参数:
            provider_name: 提供商名称
            model_name: 模型名称

        返回:
            tuple[ProviderConfig, ModelDetail] | None: 提供商配置和模型详情的元组，
                如果未找到则返回 None
        """
        provider = self.get_provider_by_name(provider_name)
        if not provider:
            return None

        for model in provider.models:
            if model.model_name == model_name:
                return provider, model
        return None

    def list_available_models(self) -> list[dict[str, Any]]:
        """列出所有可用的模型

        返回:
            list[dict[str, Any]]: 模型信息列表
        """
        models = []
        for provider in self.providers:
            for model in provider.models:
                models.append(
                    {
                        "provider_name": provider.name,
                        "model_name": model.model_name,
                        "full_name": f"{provider.name}/{model.model_name}",
                        "is_available": model.is_available,
                        "is_embedding_model": model.is_embedding_model,
                        "api_type": provider.api_type,
                    }
                )
        return models

    def validate_model_name(self, provider_model_name: str) -> bool:
        """验证模型名称格式是否正确

        参数:
            provider_model_name: 格式为 "ProviderName/ModelName" 的字符串

        返回:
            bool: 是否有效
        """
        if not provider_model_name or "/" not in provider_model_name:
            return False

        parts = provider_model_name.split("/", 1)
        if len(parts) != 2:
            return False

        provider_name, model_name = parts
        return (
            self.get_model_by_provider_and_name(provider_name, model_name) is not None
        )


def get_ai_config():
    """获取 AI 配置组"""
    return Config.get(AI_CONFIG_GROUP)


def get_default_providers() -> list[dict[str, Any]]:
    """获取默认的提供商配置

    返回:
        list[dict[str, Any]]: 默认提供商配置列表
    """
    return [
        {
            "name": "DeepSeek",
            "api_key": "YOUR_ARK_API_KEY",
            "api_base": "https://api.deepseek.com",
            "api_type": "openai",
            "models": [
                {
                    "model_name": "deepseek-chat",
                    "max_tokens": 4096,
                    "temperature": 0.7,
                },
                {
                    "model_name": "deepseek-reasoner",
                },
            ],
        },
        {
            "name": "ARK",
            "api_key": "YOUR_ARK_API_KEY",
            "api_base": "https://ark.cn-beijing.volces.com",
            "api_type": "ark",
            "models": [
                {"model_name": "deepseek-r1-250528"},
                {"model_name": "doubao-seed-1-6-250615"},
                {"model_name": "doubao-seed-1-6-flash-250615"},
                {"model_name": "doubao-seed-1-6-thinking-250615"},
            ],
        },
        {
            "name": "siliconflow",
            "api_key": "YOUR_ARK_API_KEY",
            "api_base": "https://api.siliconflow.cn",
            "api_type": "openai",
            "models": [
                {"model_name": "deepseek-ai/DeepSeek-V3"},
            ],
        },
        {
            "name": "GLM",
            "api_key": "YOUR_ARK_API_KEY",
            "api_base": "https://open.bigmodel.cn",
            "api_type": "zhipu",
            "models": [
                {"model_name": "glm-4-flash"},
                {"model_name": "glm-4-plus"},
            ],
        },
        {
            "name": "Gemini",
            "api_key": [
                "AIzaSy*****************************",
                "AIzaSy*****************************",
                "AIzaSy*****************************",
            ],
            "api_base": "https://generativelanguage.googleapis.com",
            "api_type": "gemini",
            "models": [
                {"model_name": "gemini-2.0-flash"},
                {"model_name": "gemini-2.5-flash"},
                {"model_name": "gemini-2.5-pro"},
                {"model_name": "gemini-2.5-flash-lite-preview-06-17"},
            ],
        },
    ]


def register_llm_configs():
    """注册 LLM 服务的配置项"""
    logger.info("注册 LLM 服务的配置项")

    llm_config = LLMConfig()

    Config.add_plugin_config(
        AI_CONFIG_GROUP,
        "default_model_name",
        llm_config.default_model_name,
        help="LLM服务全局默认使用的模型名称 (格式: ProviderName/ModelName)",
        type=str,
    )
    Config.add_plugin_config(
        AI_CONFIG_GROUP,
        "proxy",
        llm_config.proxy,
        help="LLM服务请求使用的网络代理，例如 http://127.0.0.1:7890",
        type=str,
    )
    Config.add_plugin_config(
        AI_CONFIG_GROUP,
        "timeout",
        llm_config.timeout,
        help="LLM服务API请求超时时间（秒）",
        type=int,
    )
    Config.add_plugin_config(
        AI_CONFIG_GROUP,
        "max_retries_llm",
        llm_config.max_retries_llm,
        help="LLM服务请求失败时的最大重试次数",
        type=int,
    )
    Config.add_plugin_config(
        AI_CONFIG_GROUP,
        "retry_delay_llm",
        llm_config.retry_delay_llm,
        help="LLM服务请求重试的基础延迟时间（秒）",
        type=int,
    )
    Config.add_plugin_config(
        AI_CONFIG_GROUP,
        "gemini_safety_threshold",
        "BLOCK_MEDIUM_AND_ABOVE",
        help=(
            "Gemini 安全过滤阈值 "
            "(BLOCK_LOW_AND_ABOVE: 阻止低级别及以上, "
            "BLOCK_MEDIUM_AND_ABOVE: 阻止中等级别及以上, "
            "BLOCK_ONLY_HIGH: 只阻止高级别, "
            "BLOCK_NONE: 不阻止)"
        ),
        type=str,
    )

    Config.add_plugin_config(
        AI_CONFIG_GROUP,
        PROVIDERS_CONFIG_KEY,
        get_default_providers(),
        help="配置多个 AI 服务提供商及其模型信息",
        default_value=[],
        type=list[ProviderConfig],
    )


@lru_cache(maxsize=1)
def get_llm_config() -> LLMConfig:
    """获取 LLM 配置实例，不再加载 MCP 工具配置"""
    ai_config = get_ai_config()

    config_data = {
        "default_model_name": ai_config.get("default_model_name"),
        "proxy": ai_config.get("proxy"),
        "timeout": ai_config.get("timeout", 180),
        "max_retries_llm": ai_config.get("max_retries_llm", 3),
        "retry_delay_llm": ai_config.get("retry_delay_llm", 2),
        PROVIDERS_CONFIG_KEY: ai_config.get(PROVIDERS_CONFIG_KEY, []),
    }

    return parse_as(LLMConfig, config_data)


def get_gemini_safety_threshold() -> str:
    """获取 Gemini 安全过滤阈值配置

    返回:
        str: 安全过滤阈值
    """
    ai_config = get_ai_config()
    return ai_config.get("gemini_safety_threshold", "BLOCK_MEDIUM_AND_ABOVE")


def validate_llm_config() -> tuple[bool, list[str]]:
    """验证 LLM 配置的有效性

    返回:
        tuple[bool, list[str]]: (是否有效, 错误信息列表)
    """
    errors = []

    try:
        llm_config = get_llm_config()

        if llm_config.timeout <= 0:
            errors.append("timeout 必须大于 0")

        if llm_config.max_retries_llm < 0:
            errors.append("max_retries_llm 不能小于 0")

        if llm_config.retry_delay_llm <= 0:
            errors.append("retry_delay_llm 必须大于 0")

        if not llm_config.providers:
            errors.append("至少需要配置一个 AI 服务提供商")
        else:
            provider_names = set()
            for provider in llm_config.providers:
                if provider.name in provider_names:
                    errors.append(f"提供商名称重复: {provider.name}")
                provider_names.add(provider.name)

                if not provider.api_key:
                    errors.append(f"提供商 {provider.name} 缺少 API Key")

                if not provider.models:
                    errors.append(f"提供商 {provider.name} 没有配置任何模型")
                else:
                    model_names = set()
                    for model in provider.models:
                        if model.model_name in model_names:
                            errors.append(
                                f"提供商 {provider.name} 中模型名称重复: "
                                f"{model.model_name}"
                            )
                        model_names.add(model.model_name)

        if llm_config.default_model_name:
            if not llm_config.validate_model_name(llm_config.default_model_name):
                errors.append(
                    f"默认模型 {llm_config.default_model_name} 在配置中不存在"
                )

    except Exception as e:
        errors.append(f"配置解析失败: {e!s}")

    return len(errors) == 0, errors


def set_default_model(provider_model_name: str | None) -> bool:
    """设置默认模型

    参数:
        provider_model_name: 模型名称，格式为 "ProviderName/ModelName"，None 表示清除

    返回:
        bool: 是否设置成功
    """
    if provider_model_name:
        llm_config = get_llm_config()
        if not llm_config.validate_model_name(provider_model_name):
            logger.error(f"模型 {provider_model_name} 在配置中不存在")
            return False

    Config.set_config(
        AI_CONFIG_GROUP, "default_model_name", provider_model_name, auto_save=True
    )

    if provider_model_name:
        logger.info(f"默认模型已设置为: {provider_model_name}")
    else:
        logger.info("默认模型已清除")

    return True


@PriorityLifecycle.on_startup(priority=10)
async def _init_llm_config_on_startup():
    """
    在服务启动时主动调用一次 get_llm_config 和 key_store.initialize，
    并预热工具提供者管理器。
    """
    logger.info("正在初始化 LLM 配置并加载密钥状态...")
    try:
        get_llm_config()
        await key_store.initialize()
        logger.debug("LLM 配置和密钥状态初始化完成。")

        logger.debug("正在预热 LLM 工具提供者管理器...")
        await tool_provider_manager.initialize()
        logger.debug("LLM 工具提供者管理器预热完成。")

    except Exception as e:
        logger.error(f"LLM 配置或密钥状态初始化时发生错误: {e}", e=e)
