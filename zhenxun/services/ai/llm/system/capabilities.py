import fnmatch

from zhenxun.services.ai.core.models import (
    ModelCapabilities,
    ModelModality,
    ReasoningMode,
)
from zhenxun.utils.pydantic_compat import model_copy

CTX_1M = 1_000_000
CTX_400K = 400_000
CTX_256K = 256_000
CTX_200K = 204_800
CTX_128K = 128_000
CTX_8K = 8_192

CAP_MULTIMODAL_EMBEDDING = ModelCapabilities(
    input_modalities={
        ModelModality.TEXT,
        ModelModality.IMAGE,
        ModelModality.AUDIO,
        ModelModality.VIDEO,
        ModelModality.FILE,
    },
    is_embedding_model=True,
    supports_tool_calling=False,
)

STANDARD_TEXT_TOOL_CAPABILITIES = ModelCapabilities(
    input_modalities={ModelModality.TEXT},
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
    supported_native_tools={
        "web_search",
        "code_execution",
        "computer_use",
        "file_search",
    },
)
CAP_GEMINI_2_5 = ModelCapabilities(
    input_modalities={
        ModelModality.TEXT,
        ModelModality.IMAGE,
        ModelModality.AUDIO,
        ModelModality.VIDEO,
    },
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
    reasoning_mode=ReasoningMode.BUDGET,
    reasoning_visibility="visible",
    supported_native_tools={
        "web_search",
        "code_execution",
        "google_map",
        "url_context",
    },
)
CAP_GEMINI_3_BASE = ModelCapabilities(
    input_modalities={
        ModelModality.TEXT,
        ModelModality.IMAGE,
        ModelModality.AUDIO,
        ModelModality.VIDEO,
    },
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
    reasoning_mode=ReasoningMode.LEVEL,
    reasoning_visibility="visible",
    supported_native_tools={
        "web_search",
        "code_execution",
        "google_map",
        "url_context",
    },
    features={
        "mixed_tools",
        "server_side_tool_invocations",
    },
)

CAP_GEMINI_3_PRO = model_copy(
    CAP_GEMINI_3_BASE,
    update={
        "reasoning_effort_map": {
            "max": "high",
            "xhigh": "high",
            "minimal": "low",
            "none": "low",
        }
    },
)

CAP_GEMINI_3_FLASH = model_copy(
    CAP_GEMINI_3_BASE,
    update={
        "reasoning_effort_map": {"max": "high", "xhigh": "high", "none": "minimal"}
    },
)
CAP_OPENAI_REASONING = ModelCapabilities(
    input_modalities={ModelModality.TEXT, ModelModality.IMAGE},
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
    reasoning_mode=ReasoningMode.EFFORT,
    reasoning_visibility="hidden",
    supported_native_tools={
        "web_search",
        "code_execution",
        "computer_use",
        "file_search",
    },
    reasoning_effort_map={"max": "xhigh", "minimal": "none"},
)
CAP_OPENAI_MULTIMODAL = ModelCapabilities(
    input_modalities={ModelModality.TEXT, ModelModality.IMAGE},
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
    supported_native_tools={
        "web_search",
        "computer_use",
        "file_search",
    },
    reasoning_effort_map={"max": "xhigh", "minimal": "none"},
)
CAP_DEEPSEEK_V4 = ModelCapabilities(
    input_modalities={ModelModality.TEXT},
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
    reasoning_mode=ReasoningMode.EFFORT,
    reasoning_visibility="visible",
    reasoning_effort_map={"minimal": "low"},
)
CAP_MINIMAX_REASONING = ModelCapabilities(
    input_modalities={ModelModality.TEXT},
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
    reasoning_mode=ReasoningMode.EFFORT,
    reasoning_visibility="visible",
)

CAP_GLM_MULTIMODAL = ModelCapabilities(
    input_modalities={
        ModelModality.TEXT,
        ModelModality.IMAGE,
        ModelModality.VIDEO,
        ModelModality.FILE,
    },
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
)

CAP_MINIMAX_MULTIMODAL = ModelCapabilities(
    input_modalities={ModelModality.TEXT, ModelModality.IMAGE, ModelModality.VIDEO},
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
)

CAP_MIMO_TEXT = ModelCapabilities(
    input_modalities={ModelModality.TEXT},
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
    supported_native_tools={"web_search"},
    reasoning_effort_map={"max": "high", "xhigh": "high", "minimal": "low"},
)

CAP_MIMO_MULTIMODAL = ModelCapabilities(
    input_modalities={
        ModelModality.TEXT,
        ModelModality.IMAGE,
        ModelModality.AUDIO,
        ModelModality.VIDEO,
    },
    output_modalities={ModelModality.TEXT, ModelModality.AUDIO},
    supports_tool_calling=True,
    supported_native_tools={"web_search"},
    reasoning_effort_map={"max": "high", "xhigh": "high", "minimal": "low"},
)


CAP_OPENAI_TTS = ModelCapabilities(
    input_modalities={ModelModality.TEXT},
    output_modalities={ModelModality.AUDIO},
    supports_tool_calling=False,
    default_voice_id="alloy"
)

CAP_GEMINI_TTS = ModelCapabilities(
    input_modalities={ModelModality.TEXT},
    output_modalities={ModelModality.AUDIO},
    supports_tool_calling=False,
    default_voice_id="Aoede"
)

CAP_MINIMAX_TTS = ModelCapabilities(
    input_modalities={ModelModality.TEXT},
    output_modalities={ModelModality.AUDIO},
    supports_tool_calling=False,
    default_voice_id="female-shaonv"
)

CAP_MIMO_TTS = ModelCapabilities(
    input_modalities={ModelModality.TEXT},
    output_modalities={ModelModality.AUDIO},
    supports_tool_calling=False,
    default_voice_id="mimo_default"
)

CAP_TEXT_EMBEDDING = ModelCapabilities(
    input_modalities={ModelModality.TEXT},
    is_embedding_model=True,
    supports_tool_calling=False,
)

CAP_RERANK_ONLY = ModelCapabilities(
    input_modalities={ModelModality.TEXT, ModelModality.IMAGE},
    is_rerank_model=True,
)

CAP_OPENAI_IMAGE = ModelCapabilities(
    input_modalities={ModelModality.TEXT, ModelModality.IMAGE},
    output_modalities={ModelModality.TEXT, ModelModality.IMAGE},
    supports_tool_calling=False,
)

CAP_GEMINI_IMAGE = ModelCapabilities(
    input_modalities={
        ModelModality.TEXT,
        ModelModality.IMAGE,
        ModelModality.AUDIO,
        ModelModality.VIDEO,
    },
    output_modalities={ModelModality.TEXT, ModelModality.IMAGE},
    supports_tool_calling=True,
    supported_native_tools={
        "web_search",
    },
)

DEFAULT_PERMISSIVE_CAPABILITIES = ModelCapabilities(
    input_modalities={
        ModelModality.TEXT,
        ModelModality.IMAGE,
        ModelModality.AUDIO,
        ModelModality.VIDEO,
    },
    output_modalities={
        ModelModality.TEXT,
        ModelModality.IMAGE,
        ModelModality.AUDIO,
    },
    supports_tool_calling=True,
)

MODEL_ALIAS_MAPPING: dict[str, str] = {
    "*DeepSeek-V4-Pro*": "deepseek-v4-pro",
    "*DeepSeek-V4-Flash*": "deepseek-v4-flash",
}


_ROUTING_TABLE: list[tuple[list[str], ModelCapabilities, int]] = [
    (["mimo-*tts*"], CAP_MIMO_TTS, CTX_8K),
    (["gemini-*tts*"], CAP_GEMINI_TTS, CTX_8K),
    (["*minimax-*tts*", "*MiniMax-*tts*"], CAP_MINIMAX_TTS, CTX_8K),
    (["*tts*"], CAP_OPENAI_TTS, CTX_8K),
    (["*gpt*image*"], CAP_OPENAI_IMAGE, CTX_128K),
    (["*gemini*image*", "*nano-banana*"], CAP_GEMINI_IMAGE, CTX_128K),
    (["glm-4.6v*"], CAP_GLM_MULTIMODAL, CTX_128K),
    (["glm-4.7-flash*"], STANDARD_TEXT_TOOL_CAPABILITIES, CTX_128K),
    (["deepseek-v4-pro*", "deepseek-v4-flash*"], CAP_DEEPSEEK_V4, CTX_1M),
    (["glm-4-long*"], STANDARD_TEXT_TOOL_CAPABILITIES, CTX_1M),
    (["*MiniMax-M3*"], CAP_MINIMAX_MULTIMODAL, CTX_1M),
    (["mimo-v2.5-pro*", "mimo-v2-pro*", "mimo-v2-flash*"], CAP_MIMO_TEXT, CTX_1M),
    (["mimo-v2.5", "mimo-v2-omni*"], CAP_MIMO_MULTIMODAL, CTX_1M),
    (["gpt-5.5*", "gpt-5.4*"], CAP_OPENAI_MULTIMODAL, CTX_1M),
    (["gemini-3*pro*"], CAP_GEMINI_3_PRO, CTX_1M),
    (["gemini-3*"], CAP_GEMINI_3_FLASH, CTX_1M),
    (
        ["gemini-2.5-pro*", "gemini-2.5-flash*"],
        CAP_GEMINI_2_5,
        CTX_1M,
    ),
    (
        ["gpt-5*", "gpt-5-mini*", "gpt-5-nano*", "*codex*"],
        CAP_OPENAI_MULTIMODAL,
        CTX_400K,
    ),
    (
        ["kimi-k2.7*", "kimi-k2.6*", "kimi-k2.5*"],
        DEFAULT_PERMISSIVE_CAPABILITIES,
        CTX_256K,
    ),
    (["glm-5v*"], CAP_GLM_MULTIMODAL, CTX_200K),
    (["glm-5*", "glm-4.7*", "glm-4.6*"], STANDARD_TEXT_TOOL_CAPABILITIES, CTX_200K),
    (["*MiniMax-M2*", "*minimax-m2*"], CAP_MINIMAX_REASONING, CTX_200K),
    (["gpt-4*", "gpt-3.5*", "gpt-*"], CAP_OPENAI_MULTIMODAL, CTX_128K),
    (["o1-*", "o3-*"], CAP_OPENAI_REASONING, CTX_128K),
    (["glm-4v*"], CAP_GLM_MULTIMODAL, CTX_128K),
    (
        ["glm-4.5*", "glm-4-flashx-*", "glm-4*"],
        STANDARD_TEXT_TOOL_CAPABILITIES,
        CTX_128K,
    ),
    (
        ["gemini-embedding-2*", "jina-embeddings-v5-omni*"],
        CAP_MULTIMODAL_EMBEDDING,
        CTX_8K,
    ),
    (
        ["*embedding*", "*Embedding*", "jina-embeddings-*", "bge-m3*", "*bge-large*"],
        CAP_TEXT_EMBEDDING,
        CTX_8K,
    ),
    (["*reranker*", "*rerank*", "jina-colbert-*"], CAP_RERANK_ONLY, CTX_8K),
]


def _build_registry() -> dict[str, ModelCapabilities]:
    """构建模型能力注册表 (基于声明式路由表)"""
    registry: dict[str, ModelCapabilities] = {}

    for patterns, cap_template, ctx_limit in _ROUTING_TABLE:
        cap_instance = model_copy(cap_template, update={"max_input_tokens": ctx_limit})
        for pattern in patterns:
            registry[pattern] = cap_instance

    return registry


MODEL_CAPABILITIES_REGISTRY = _build_registry()


def get_model_capabilities(model_name: str) -> ModelCapabilities:
    """
    从注册表获取模型能力，支持别名映射和通配符匹配。
    """
    canonical_name = model_name
    for alias_pattern, c_name in MODEL_ALIAS_MAPPING.items():
        if fnmatch.fnmatch(model_name, alias_pattern):
            canonical_name = c_name
            break

    parts = canonical_name.split("/")
    names_to_check = ["/".join(parts[i:]) for i in range(len(parts))]

    for name in names_to_check:
        if name in MODEL_CAPABILITIES_REGISTRY:
            return MODEL_CAPABILITIES_REGISTRY[name]

        for pattern, capabilities in MODEL_CAPABILITIES_REGISTRY.items():
            if "*" in pattern and fnmatch.fnmatch(name, pattern):
                return capabilities

    return DEFAULT_PERMISSIVE_CAPABILITIES
