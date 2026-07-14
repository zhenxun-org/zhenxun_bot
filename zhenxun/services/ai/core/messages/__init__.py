"""
消息与响应域类型定义 - 统一导出门面
"""

from zhenxun.utils.pydantic_compat import model_rebuild

from .context_events import (
    AgentEvent,
    HandoffEvent,
    TaskLifecycleEvent,
)
from .models import (
    AssistantMessage,
    LLMMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from .parts import (
    AudioPart,
    BaseContentPart,
    EmbedBatch,
    EmbedPayload,
    FilePart,
    ImagePart,
    LLMContentPart,
    TextDeltaPart,
    TextPart,
    ThoughtDeltaPart,
    ThoughtPart,
    ToolCallDeltaPart,
    ToolCallPart,
    ToolReturnPart,
    VideoPart,
)
from .requests import (
    BaseRequest,
    ChatRequest,
    EmbeddingRequest,
    ImageRequest,
    RerankRequest,
    SpeechRequest,
)
from .responses import (
    AudioResponse,
    ChatResponse,
    EmbeddingResponse,
    ImageResponse,
    RerankResponse,
)
from .shared import (
    LLMCodeExecution,
    LLMGroundingAttribution,
    LLMGroundingMetadata,
    RerankDocument,
    RerankResult,
    UsageInfo,
)
from .types import (
    AgentMessage,
    AnyLLMMessage,
    ContentT,
    PromptInput,
    RoleT,
    UserContentUnion,
)

model_rebuild(ChatResponse)
model_rebuild(LLMMessage)
model_rebuild(SystemMessage)
model_rebuild(UserMessage)
model_rebuild(AssistantMessage)
model_rebuild(ToolMessage)
model_rebuild(RerankResponse)


__all__ = [
    "AgentEvent",
    "AgentMessage",
    "AnyLLMMessage",
    "AssistantMessage",
    "AudioPart",
    "AudioResponse",
    "BaseContentPart",
    "BaseRequest",
    "ChatRequest",
    "ChatResponse",
    "ContentT",
    "EmbedBatch",
    "EmbedPayload",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "FilePart",
    "HandoffEvent",
    "ImagePart",
    "ImageRequest",
    "ImageResponse",
    "LLMCodeExecution",
    "LLMContentPart",
    "LLMGroundingAttribution",
    "LLMGroundingMetadata",
    "LLMMessage",
    "PromptInput",
    "RerankDocument",
    "RerankRequest",
    "RerankResponse",
    "RerankResult",
    "RoleT",
    "SpeechRequest",
    "SystemMessage",
    "TaskLifecycleEvent",
    "TextDeltaPart",
    "TextPart",
    "ThoughtDeltaPart",
    "ThoughtPart",
    "ToolCallDeltaPart",
    "ToolCallPart",
    "ToolMessage",
    "ToolReturnPart",
    "UsageInfo",
    "UserContentUnion",
    "UserMessage",
    "VideoPart",
]
