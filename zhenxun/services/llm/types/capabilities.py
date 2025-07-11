"""
LLM 模型能力定义模块

定义模型的输入输出模态、工具调用支持等核心能力。
"""

from enum import Enum
import fnmatch

from pydantic import BaseModel, Field


class ModelModality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    EMBEDDING = "embedding"


class ModelCapabilities(BaseModel):
    """定义一个模型的核心、稳定能力。"""

    input_modalities: set[ModelModality] = Field(default={ModelModality.TEXT})
    output_modalities: set[ModelModality] = Field(default={ModelModality.TEXT})
    supports_tool_calling: bool = False
    is_embedding_model: bool = False


STANDARD_TEXT_TOOL_CAPABILITIES = ModelCapabilities(
    input_modalities={ModelModality.TEXT},
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
)

GEMINI_CAPABILITIES = ModelCapabilities(
    input_modalities={
        ModelModality.TEXT,
        ModelModality.IMAGE,
        ModelModality.AUDIO,
        ModelModality.VIDEO,
    },
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
)

DOUBAO_ADVANCED_MULTIMODAL_CAPABILITIES = ModelCapabilities(
    input_modalities={ModelModality.TEXT, ModelModality.IMAGE, ModelModality.VIDEO},
    output_modalities={ModelModality.TEXT},
    supports_tool_calling=True,
)


MODEL_ALIAS_MAPPING: dict[str, str] = {
    "deepseek-v3*": "deepseek-chat",
    "deepseek-ai/DeepSeek-V3": "deepseek-chat",
    "deepseek-r1*": "deepseek-reasoner",
}


MODEL_CAPABILITIES_REGISTRY: dict[str, ModelCapabilities] = {
    "gemini-*-tts": ModelCapabilities(
        input_modalities={ModelModality.TEXT},
        output_modalities={ModelModality.AUDIO},
    ),
    "gemini-*-native-audio-*": ModelCapabilities(
        input_modalities={ModelModality.TEXT, ModelModality.AUDIO, ModelModality.VIDEO},
        output_modalities={ModelModality.TEXT, ModelModality.AUDIO},
        supports_tool_calling=True,
    ),
    "gemini-2.0-flash-preview-image-generation": ModelCapabilities(
        input_modalities={
            ModelModality.TEXT,
            ModelModality.IMAGE,
            ModelModality.AUDIO,
            ModelModality.VIDEO,
        },
        output_modalities={ModelModality.TEXT, ModelModality.IMAGE},
        supports_tool_calling=True,
    ),
    "gemini-embedding-exp": ModelCapabilities(
        input_modalities={ModelModality.TEXT},
        output_modalities={ModelModality.EMBEDDING},
        is_embedding_model=True,
    ),
    "gemini-2.5-pro*": GEMINI_CAPABILITIES,
    "gemini-1.5-pro*": GEMINI_CAPABILITIES,
    "gemini-2.5-flash*": GEMINI_CAPABILITIES,
    "gemini-2.0-flash*": GEMINI_CAPABILITIES,
    "gemini-1.5-flash*": GEMINI_CAPABILITIES,
    "GLM-4V-Flash": ModelCapabilities(
        input_modalities={ModelModality.TEXT, ModelModality.IMAGE},
        output_modalities={ModelModality.TEXT},
        supports_tool_calling=True,
    ),
    "GLM-4V-Plus*": ModelCapabilities(
        input_modalities={ModelModality.TEXT, ModelModality.IMAGE, ModelModality.VIDEO},
        output_modalities={ModelModality.TEXT},
        supports_tool_calling=True,
    ),
    "glm-4-*": STANDARD_TEXT_TOOL_CAPABILITIES,
    "glm-z1-*": STANDARD_TEXT_TOOL_CAPABILITIES,
    "doubao-seed-*": DOUBAO_ADVANCED_MULTIMODAL_CAPABILITIES,
    "doubao-1-5-thinking-vision-pro": DOUBAO_ADVANCED_MULTIMODAL_CAPABILITIES,
    "deepseek-chat": STANDARD_TEXT_TOOL_CAPABILITIES,
    "deepseek-reasoner": STANDARD_TEXT_TOOL_CAPABILITIES,
}


def get_model_capabilities(model_name: str) -> ModelCapabilities:
    """
    从注册表获取模型能力，支持别名映射和通配符匹配。
    查找顺序: 1. 标准化名称 -> 2. 精确匹配 -> 3. 通配符匹配 -> 4. 默认值
    """
    canonical_name = model_name
    for alias_pattern, c_name in MODEL_ALIAS_MAPPING.items():
        if fnmatch.fnmatch(model_name, alias_pattern):
            canonical_name = c_name
            break

    if canonical_name in MODEL_CAPABILITIES_REGISTRY:
        return MODEL_CAPABILITIES_REGISTRY[canonical_name]

    for pattern, capabilities in MODEL_CAPABILITIES_REGISTRY.items():
        if "*" in pattern and fnmatch.fnmatch(model_name, pattern):
            return capabilities

    return ModelCapabilities()
