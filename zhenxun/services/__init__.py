"""
Zhenxun Bot - 核心服务模块

主要服务包括：
- 数据库上下文 (db_context): 提供数据库模型基类和连接管理。
- 日志服务 (log): 提供增强的、带上下文的日志记录器。
- LLM服务 (llm): 提供与大语言模型交互的统一API。
- 插件生命周期管理 (plugin_init): 支持插件安装和卸载时的钩子函数。
- 定时任务调度器 (scheduler): 提供持久化的、可管理的定时任务服务。
"""

from nonebot import require

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_alconna")
require("nonebot_plugin_session")
require("nonebot_plugin_htmlrender")
require("nonebot_plugin_uninfo")
require("nonebot_plugin_waiter")

from .db_context import Model, disconnect, with_db_timeout
from .llm import (
    AI,
    AIConfig,
    CommonOverrides,
    LLMContentPart,
    LLMException,
    LLMGenerationConfig,
    LLMMessage,
    analyze,
    analyze_multimodal,
    chat,
    clear_model_cache,
    code,
    create_multimodal_message,
    embed,
    generate,
    get_cache_stats,
    get_model_instance,
    list_available_models,
    list_embedding_models,
    pipeline_chat,
    search,
    search_multimodal,
    set_global_default_model_name,
    tool_registry,
)
from .log import logger
from .plugin_init import PluginInit, PluginInitManager
from .scheduler import scheduler_manager

__all__ = [
    "AI",
    "AIConfig",
    "CommonOverrides",
    "LLMContentPart",
    "LLMException",
    "LLMGenerationConfig",
    "LLMMessage",
    "Model",
    "PluginInit",
    "PluginInitManager",
    "analyze",
    "analyze_multimodal",
    "chat",
    "clear_model_cache",
    "code",
    "create_multimodal_message",
    "disconnect",
    "embed",
    "generate",
    "get_cache_stats",
    "get_model_instance",
    "list_available_models",
    "list_embedding_models",
    "logger",
    "pipeline_chat",
    "scheduler_manager",
    "search",
    "search_multimodal",
    "set_global_default_model_name",
    "tool_registry",
    "with_db_timeout",
]
