from functools import lru_cache
from typing import Any

from zhenxun.configs.config import Config
from zhenxun.configs.utils import parse_as
from zhenxun.utils.pydantic_compat import model_dump

from .models import DebugLogOptions, LLMConfig, ProviderConfig


def get_ai_config():
    """获取 AI 配置组"""
    return Config.get("AI")


def get_default_providers() -> list[dict[str, Any]]:
    """获取默认提供商配置列表。"""
    return [
        {
            "name": "DeepSeek",
            "api_key": "YOUR_API_KEY",
            "api_base": "https://api.deepseek.com",
            "api_type": "deepseek",
            "models": [
                {
                    "model_name": "deepseek-v4-pro",
                    "reasoning_effort": "high",
                },
                {
                    "model_name": "deepseek-v4-flash",
                },
            ],
        },
        {
            "name": "Doubao",
            "api_key": "YOUR_ARK_API_KEY",
            "api_base": "https://ark.cn-beijing.volces.com/api",
            "api_type": "doubao",
            "models": [
                {"model_name": "doubao-seed-1-6-250615"},
                {"model_name": "doubao-seed-1-6-flash-250615"},
            ],
        },
        {
            "name": "siliconflow",
            "api_key": "YOUR_ARK_API_KEY",
            "api_base": "https://api.siliconflow.cn",
            "api_type": "openai",
            "models": [
                {"model_name": "deepseek-ai/DeepSeek-V4-Flash"},
                {"model_name": "BAAI/bge-m3"},
                {"model_name": "BAAI/bge-reranker-v2-m3"},
            ],
        },
        {
            "name": "GLM",
            "api_key": "YOUR_API_KEY",
            "api_base": "https://open.bigmodel.cn",
            "api_type": "glm",
            "models": [
                {"model_name": "glm-4.6v-flash"},
                {"model_name": "glm-5v-turbo"},
            ],
        },
        {
            "name": "Gemini",
            "api_key": [
                "AIzaSy*****************************",
                "AIzaSy*****************************",
            ],
            "api_base": "https://generativelanguage.googleapis.com",
            "api_type": "gemini",
            "models": [
                {"model_name": "gemini-3.5-flash"},
                {"model_name": "gemini-3.1-flash-lite"},
                {"model_name": "gemini-2.5-flash-image"},
                {"model_name": "gemini-embedding-2"},
                {"model_name": "gemini-3.1-flash-tts-preview"},
            ],
        },
        {
            "name": "OpenRouter",
            "api_key": "YOUR_OPENROUTER_API_KEY",
            "api_base": "https://openrouter.ai/api",
            "api_type": "openrouter",
            "models": [
                {"model_name": "google/gemini-3.1-flash-lite"},
                {"model_name": "x-ai/grok-4"},
            ],
        },
        {
            "name": "MiniMax",
            "api_key": "YOUR_API_KEY",
            "api_base": "https://api.minimaxi.com",
            "api_type": "minimax",
            "models": [
                {"model_name": "MiniMax-M3"},
                {"model_name": "MiniMax-M2.7"},
                {"model_name": "MiniMax-M2.7-highspeed"},
            ],
        },
        {
            "name": "MiMo",
            "api_key": "YOUR_MIMO_API_KEY",
            "api_base": "https://api.xiaomimimo.com",
            "api_type": "mimo",
            "models": [
                {"model_name": "mimo-v2.5-pro"},
                {"model_name": "mimo-v2.5"},
                {"model_name": "mimo-v2.5-tts"},
            ],
        },
    ]


def register_llm_configs():
    """注册 LLM 服务的配置项"""

    llm_config = LLMConfig()

    Config.add_plugin_config(
        "AI",
        "default_models",
        model_dump(llm_config.default_models),
        help="不同任务类型的全局默认模型配置字典",
        type=dict,
    )
    Config.add_plugin_config(
        "AI",
        "client_settings",
        model_dump(llm_config.client_settings),
        help=(
            "LLM客户端高级设置。\n"
            "包含: timeout(超时秒数), max_retries(重试次数), "
            "retry_delay(重试延迟), structured_retries(结构化生成重试)"
        ),
        type=dict,
    )
    Config.add_plugin_config(
        "AI",
        "debug_log",
        model_dump(llm_config.debug_log),
        help=(
            "LLM日志详情开关。示例: {'show_tools': True, 'show_schema': False, "
            "'show_safety': False}"
        ),
        type=dict,
    )

    Config.add_plugin_config(
        "AI",
        "context_settings",
        model_dump(llm_config.context_settings),
        help=(
            "智能上下文管理与压缩配置。\n"
            "包含:\n"
            "  - llm_summary: 大模型总结策略配置\n"
            "    - enable: 是否开启大模型对话总结以压缩上下文\n"
            "    - trigger_threshold: 触发压缩的 Token 阈值。<=1.0为比例，>1.0为绝对 Token 数\n"  # noqa: E501
            "    - max_history_turns: 触发压缩的最大历史对话轮数\n"
            "    - summarization_model: 指定用于总结的大模型名称\n"
            "    - summarization_prompt: 指导大模型总结的系统提示词\n"
            "    - keep_recent_turns: 总结外强制原样保留的最近对话轮数\n"
            "  - vision_window_size: 多模态滑动窗口大小。0表示无限制，>0表示仅保留最近N轮包含多模态真实数据的消息，超过则自动降级为占位符\n"  # noqa: E501
            "  - tool_pruning: 工具结果过载修剪策略配置\n"
            "    - enable: 是否开启长工具输出结果的自动修剪\n"
            "    - trigger_threshold: 触发修剪的工具纯 Token 阈值。<=1.0为比例，>1.0为绝对 Token 数\n"  # noqa: E501
            "    - max_history_turns: 触发修剪的最大工具消息轮数。设为 0 表示不限制轮数\n"  # noqa: E501
            "    - keep_recent_turns: 修剪时强制原样保留的最新的工具消息轮数"
        ),
        type=dict,
    )

    Config.add_plugin_config(
        "AI",
        "MODEL_GROUPS",
        llm_config.model_groups,
        help=(
            "虚拟模型路由组配置 (Virtual Router Groups)。\n"
            "键为组名，值为模型名称或其它组名的列表。\n"
            "使用 chat(model='cheap_models') 时系统将自动按列表顺序轮询和故障转移。"
        ),
        type=dict,
    )

    Config.add_plugin_config(
        "AI",
        "agent_settings",
        model_dump(llm_config.agent_settings),
        help=(
            "Agent 执行引擎默认设置。\n"
            "包含: max_cycles(最大工具循环数), enable_parallel_calls(允许并行), "
            "reflexion_retries(反思重试次数), "
            "enable_fallback_summary(达到最大循环时兜底总结), "
            "enable_hitl(是否允许智能体主动向用户求助), "
            "mcp_cleanup_timeout(MCP 闲置回收时间)"
        ),
        type=dict,
    )

    Config.add_plugin_config(
        "AI",
        "sandbox",
        model_dump(llm_config.sandbox),
        help=(
            "沙箱底层环境基础设施配置。\n"
            "包含: enable_sandbox(全局开关), sandbox_type(驱动类型), docker_image"
            "(使用的镜像), "
            "cleanup_timeout(空闲清理超时秒数), enable_vfs_helper(开启VFS防逃逸探针)。"
        ),
        type=dict,
    )

    Config.add_plugin_config(
        "AI",
        "provider_settings",
        model_dump(llm_config.provider_settings),
        help=("厂商专属高级设置。\n包含各厂商全局的特有策略开关"),
        type=dict,
    )

    Config.add_plugin_config(
        "AI",
        "PROVIDERS",
        get_default_providers(),
        help=(
            "配置多个 AI 服务提供商及其模型信息。\n"
            "注意：可以在特定模型配置下添加 'api_type' 以覆盖提供商的全局设置。\n"
            "支持的 api_type 包括:\n"
            "- 'openai': 标准 OpenAI 格式 (DeepSeek, SiliconFlow等)\n"
            "- 'gemini': Google Gemini API\n"
            "- 'glm': 智谱 AI (GLM)\n"
            "- 'doubao': 字节跳动火山引擎 (Doubao)\n"
            "- 'jina': Jina AI (专精于多模态嵌入与重排)\n"
            "- 'openrouter': OpenRouter 聚合平台\n"
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
        "default_models": ai_config.get("default_models", {}),
        "client_settings": ai_config.get("client_settings", {}),
        "debug_log": debug_log_val,
        "PROVIDERS": ai_config.get("PROVIDERS", []),
        "context_settings": ai_config.get("context_settings", {}),
        "model_groups": ai_config.get("MODEL_GROUPS", {}),
        "agent_settings": ai_config.get("agent_settings", {}),
        "sandbox": ai_config.get("sandbox", {}),
        "provider_settings": ai_config.get("provider_settings", {}),
    }

    return parse_as(LLMConfig, config_data)


def get_gemini_safety_threshold() -> str:
    """获取 Gemini 安全过滤阈值配置。"""
    return get_llm_config().provider_settings.gemini.safety_threshold
