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

from .db_context import Model, disconnect
from .llm import (
    AI,
    LLMContentPart,
    LLMException,
    LLMMessage,
    get_model_instance,
    list_available_models,
    tool_registry,
)
from .log import logger
from .plugin_init import PluginInit, PluginInitManager
from .scheduler import scheduler_manager

__all__ = [
    "AI",
    "LLMContentPart",
    "LLMException",
    "LLMMessage",
    "Model",
    "PluginInit",
    "PluginInitManager",
    "disconnect",
    "get_model_instance",
    "list_available_models",
    "logger",
    "scheduler_manager",
    "tool_registry",
]
