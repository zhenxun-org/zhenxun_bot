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
from zhenxun.utils.pydantic_compat import model_dump

from ..core import key_store
from ..tools import tool_provider_manager
from ..types.models import ModelDetail, ProviderConfig

AI_CONFIG_GROUP = "AI"
PROVIDERS_CONFIG_KEY = "PROVIDERS"


class DebugLogOptions(BaseModel):
    """调试日志细粒度控制"""

    show_tools: bool = Field(
        default=True, description="是否在日志中显示工具定义(JSON Schema)"
    )
    show_schema: bool = Field(
        default=True, description="是否在日志中显示结构化输出Schema(response_format)"
    )
    show_safety: bool = Field(
        default=True, description="是否在日志中显示安全设置(safetySettings)"
    )

    def __bool__(self) -> bool:
        """支持 bool(debug_options) 的语法，方便兼容旧逻辑。"""
        return self.show_tools or self.show_schema or self.show_safety


class ClientSettings(BaseModel):
    """LLM 客户端通用设置"""

    timeout: int = Field(default=300, description="API请求超时时间（秒）")
    max_retries: int = Field(default=3, description="请求失败时的最大重试次数")
    retry_delay: int = Field(default=2, description="请求重试的基础延迟时间（秒）")
    structured_retries: int = Field(
        default=2, description="结构化生成校验失败时的最大重试次数 (IVR)"
    )
    proxy: str | None = Field(
        default=None,
        description="网络代理，例如 http://127.0.0.1:7890",
    )


class LLMConfig(BaseModel):
    """LLM 服务配置类"""

    default_model_name: str | None = Field(
        default=None,
        description="LLM服务全局默认使用的模型名称 (格式: ProviderName/ModelName)",
    )
    client_settings: ClientSettings = Field(
        default_factory=ClientSettings, description="客户端连接与重试配置"
    )
    providers: list[ProviderConfig] = Field(
        default_factory=list, description="配置多个 AI 服务提供商及其模型信息"
    )
    debug_log: DebugLogOptions | bool = Field(
        default_factory=DebugLogOptions,
        description="LLM请求日志详情开关。支持 bool (全开/全关) 或 dict (细粒度控制)。",
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
            "extra_headers": {},
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
                {"model_name": "gemini-2.5-flash"},
                {"model_name": "gemini-2.5-pro"},
                {"model_name": "gemini-2.5-flash-lite"},
            ],
        },
        {
            "name": "OpenRouter",
            "api_key": "YOUR_OPENROUTER_API_KEY",
            "api_base": "https://openrouter.ai/api",
            "api_type": "openrouter",
            "models": [
                {"model_name": "google/gemini-2.5-pro"},
                {"model_name": "google/gemini-2.5-flash"},
                {"model_name": "x-ai/grok-4"},
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
        "client_settings",
        model_dump(llm_config.client_settings),
        help=(
            "LLM客户端高级设置。\n"
            "包含: timeout(超时秒数), max_retries(重试次数), "
            "retry_delay(重试延迟), structured_retries(结构化生成重试), proxy(代理)"
        ),
        type=dict,
    )
    Config.add_plugin_config(
        AI_CONFIG_GROUP,
        "debug_log",
        {"show_tools": True, "show_schema": True, "show_safety": True},
        help=(
            "LLM日志详情开关。示例: {'show_tools': True, 'show_schema': False, "
            "'show_safety': False}"
        ),
        type=dict,
    )
    Config.add_plugin_config(
        AI_CONFIG_GROUP,
        "gemini_safety_threshold",
        "BLOCK_NONE",
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
        help=(
            "配置多个 AI 服务提供商及其模型信息。\n"
            "注意：可以在特定模型配置下添加 'api_type' 以覆盖提供商的全局设置。\n"
            "可选：在 provider 下添加 'extra_headers' 传递额外请求头，"
            "用于 AI 网关鉴权（例如 cf-aig-authorization）。\n"
            "支持的 api_type 包括:\n"
            "- 'openai': 标准 OpenAI 格式 (DeepSeek, SiliconFlow, Moonshot 等)\n"
            "- 'gemini': Google Gemini API\n"
            "- 'zhipu': 智谱 AI (GLM)\n"
            "- 'ark': 字节跳动火山引擎 (Doubao)\n"
            "- 'openrouter': OpenRouter 聚合平台\n"
            "- 'openai_image': OpenAI 兼容的图像生成接口 (DALL-E)\n"
            "- 'openai_responses': 支持新版 responses 格式的 OpenAI 兼容接口\n"
            "- 'smart': 智能路由模式 (主要用于第三方中转场景，自动根据模型名"
            "分发请求到 openai 或 gemini)"
        ),
        default_value=[],
        type=list[ProviderConfig],
    )


@lru_cache(maxsize=1)
def get_llm_config() -> LLMConfig:
    """获取 LLM 配置实例"""
    ai_config = get_ai_config()

    raw_debug = ai_config.get("debug_log", False)
    if isinstance(raw_debug, bool):
        debug_log_val = DebugLogOptions(
            show_tools=raw_debug, show_schema=raw_debug, show_safety=raw_debug
        )
    else:
        debug_log_val = raw_debug

    config_data = {
        "default_model_name": ai_config.get("default_model_name"),
        "client_settings": ai_config.get("client_settings", {}),
        "debug_log": debug_log_val,
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

        if llm_config.client_settings.timeout <= 0:
            errors.append("timeout 必须大于 0")

        if llm_config.client_settings.max_retries < 0:
            errors.append("max_retries 不能小于 0")

        if llm_config.client_settings.retry_delay <= 0:
            errors.append("retry_delay 必须大于 0")

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
