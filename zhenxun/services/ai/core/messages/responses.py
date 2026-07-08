
from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, Field

from zhenxun.utils.pydantic_compat import parse_as

from .parts import (
    AudioPart,
    ImagePart,
    LLMContentPart,
    TextPart,
    ThoughtPart,
    ToolCallPart,
    ToolReturnPart,
)
from .shared import RerankResult, UsageInfo

T = TypeVar("T", bound=BaseModel)


class ChatResponse(BaseModel):
    """
    文本/多模态对话响应对象，确立 SSOT (单一数据源) 架构。
    """

    content_parts: list[LLMContentPart] = Field(default_factory=list)
    """经由解析、统一封装后的标准内容部件列表"""
    usage_info: dict[str, Any] | None = None
    """厂商返回的原始用量遥测字段"""
    raw_response: dict[str, Any] | None = None
    """完整的 API 层级原生响应字典 (未经框架清洗)"""
    grounding_metadata: Any | None = None
    """联网搜索、位置等基底事实归因元数据"""
    parsed_obj: Any | None = Field(default=None)
    """在结构化输出模式下，由中间件反序列化得出的强类型 Pydantic 对象实例"""

    def get_parsed_obj(self, model_class: type[T]) -> T | None:
        """
        从结构化生成结果中获取强类型的 Pydantic 解析对象，提供完善的 IDE 类型推导支持。

        参数:
            model_class: 期望提取的 Pydantic 模型类。

        返回:
            强类型的模型实例，如果不存在则返回 None
        """
        if self.parsed_obj is None:
            return None
        if isinstance(self.parsed_obj, model_class):
            return self.parsed_obj

        try:
            return parse_as(model_class, self.parsed_obj)
        except Exception:
            return self.parsed_obj

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return [p for p in self.content_parts if isinstance(p, ToolCallPart)]

    @property
    def text(self) -> str:
        """动态视图：提取并拼接所有文本块"""
        return "".join(
            p.text for p in self.content_parts if isinstance(p, TextPart)
        ).strip()

    @property
    def thought_text(self) -> str | None:
        """动态视图：提取并拼接所有思考/推理块"""
        thoughts = [
            p.thought_text for p in self.content_parts if isinstance(p, ThoughtPart)
        ]
        return "\n".join(thoughts).strip() if thoughts else None

    @property
    def thought_signature(self) -> str | None:
        """动态视图：获取当前响应中的思考指纹"""
        for p in reversed(self.content_parts):
            if isinstance(p, ThoughtPart | ToolCallPart | ToolReturnPart):
                if p.metadata and "thought_signature" in p.metadata:
                    return p.metadata["thought_signature"]
        return None

    @property
    def images(self) -> list[bytes | Path | str]:
        """动态视图：提取响应中包含的所有图片数据"""
        imgs = []
        for p in self.content_parts:
            if isinstance(p, ImagePart):
                if p.url:
                    imgs.append(p.url)
                elif p.raw:
                    imgs.append(p.raw)
                elif p.path:
                    imgs.append(p.path)
        return imgs

    @property
    def audios(self) -> list[bytes | Path | str]:
        """动态视图：提取音频数据"""
        audios = []
        for p in self.content_parts:
            if isinstance(p, AudioPart):
                if p.url:
                    audios.append(p.url)
                elif p.raw:
                    audios.append(p.raw)
                elif p.path:
                    audios.append(p.path)
        return audios


class EmbeddingResponse(BaseModel):
    """Embedding 向量富响应对象"""

    embeddings: list[list[float]]
    """多段输入文本对应生成的高维浮点数向量数组 (二维)"""
    usage: UsageInfo = Field(default_factory=UsageInfo)
    """执行向量化任务产生的 Token 用量开销统计"""
    model_name: str
    """实际用于执行本次编码任务的模型名称"""

    @property
    def vector(self) -> list[float]:
        """便捷属性：当且仅当只需提取单一文本向量时，直接返回一维向量"""
        return self.embeddings[0] if self.embeddings else []


class AudioResponse(BaseModel):
    """统一的语音合成响应对象"""

    audio_bytes: bytes
    """生成的音频二进制裸数据，可直接用于发送或保存"""
    audio_format: str
    """实际返回的音频格式 (如 mp3, wav)"""
    usage: UsageInfo = Field(default_factory=UsageInfo)
    """Token 或 字符数消耗统计"""
    raw_response: Any | None = None
    """原生响应体，供高级调试使用"""
    model_name: str
    """实际执行任务的模型名称"""


class ImageResponse(BaseModel):
    """图像生成响应对象"""

    content_parts: list[LLMContentPart] = Field(default_factory=list)
    raw_response: dict[str, Any] | None = None

    @property
    def images(self) -> list[bytes | Path | str]:
        """动态视图：提取响应中包含的所有图片数据"""
        imgs = []
        for p in self.content_parts:
            if isinstance(p, ImagePart):
                if p.url:
                    imgs.append(p.url)
                elif p.raw:
                    imgs.append(p.raw)
                elif p.path:
                    imgs.append(p.path)
        return imgs

    @property
    def text(self) -> str:
        """动态视图：提取并拼接所有 TextPart 文本内容"""
        return "".join(
            p.text for p in self.content_parts if isinstance(p, TextPart)
        ).strip()


class RerankResponse(BaseModel):
    """文本重排响应对象"""

    results: list[RerankResult]
    """重排后的文档结果列表"""


__all__ = [
    "AudioResponse",
    "ChatResponse",
    "EmbeddingResponse",
    "ImageResponse",
    "RerankResponse",
]
