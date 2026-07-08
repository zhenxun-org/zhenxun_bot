from __future__ import annotations

import base64
from pathlib import Path
from typing import Annotated, Any, Literal
from typing_extensions import Self

from pydantic import BaseModel, Field

from zhenxun.services.ai.utils.logger import log_core as logger
from zhenxun.utils.pydantic_compat import model_validator


class BaseContentPart(BaseModel):
    """多态消息内容的底层基类"""

    metadata: dict[str, Any] | None = Field(default=None)
    """该部件的内部元数据，提供如 thought_signature、解析结果等非展示用数据"""

    @classmethod
    def text_part(cls, text: str) -> "TextPart":
        """创建一个纯文本片段"""
        return TextPart(text=text)

    @classmethod
    def thought_part(cls, text: str) -> "ThoughtPart":
        """创建一个思维链 (CoT) 思考片段"""
        return ThoughtPart(thought_text=text)

    @classmethod
    def image_url_part(cls, url: str) -> "ImagePart":
        """创建一个基于外网 URL 的图片片段"""
        return ImagePart(url=url)

    @classmethod
    def image_base64_part(cls, data: str, mime_type: str = "image/png") -> "ImagePart":
        """创建一个基于 Base64 编码数据的图片片段"""
        return ImagePart(raw=base64.b64decode(data), mime_type=mime_type)

    @classmethod
    def audio_url_part(cls, url: str, mime_type: str = "audio/wav") -> "AudioPart":
        """创建一个基于外网 URL 的音频片段"""
        return AudioPart(url=url, mime_type=mime_type)

    @classmethod
    def video_url_part(cls, url: str, mime_type: str = "video/mp4") -> "VideoPart":
        """创建一个基于外网 URL 的视频片段"""
        return VideoPart(url=url, mime_type=mime_type)

    @classmethod
    def video_base64_part(cls, data: str, mime_type: str = "video/mp4") -> "VideoPart":
        """创建一个基于 Base64 编码数据的视频片段"""
        return VideoPart(raw=base64.b64decode(data), mime_type=mime_type)

    @classmethod
    def audio_base64_part(cls, data: str, mime_type: str = "audio/wav") -> "AudioPart":
        """创建一个基于 Base64 编码数据的音频片段"""
        return AudioPart(raw=base64.b64decode(data), mime_type=mime_type)

    @classmethod
    def file_uri_part(
        cls,
        file_uri: str,
        mime_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "FilePart":
        """创建一个基于云端 URI (如 Google Cloud Storage gs://) 的通用文件片段"""
        return FilePart(url=file_uri, mime_type=mime_type, metadata=metadata or {})

    @classmethod
    def tool_call_part(
        cls, id: str, tool_name: str, args: dict[str, Any] | str
    ) -> "ToolCallPart":
        """创建一个工具调用请求片段"""
        return ToolCallPart(id=id, tool_name=tool_name, args=args)

    @classmethod
    def tool_return_part(cls, call_id: str, name: str, result: Any) -> "ToolReturnPart":
        """创建一个工具执行结果片段"""
        return ToolReturnPart(
            tool_call_id=call_id,
            tool_name=name,
            output=result,
        )

    async def get_raw_bytes(self) -> bytes:
        """统一获取多模态原始字节数据 (自动适配 raw、本地 path 或自动下载公网 url)"""
        raw_data = getattr(self, "raw", None)
        if isinstance(raw_data, bytes):
            return raw_data

        path_data = getattr(self, "path", None)
        if path_data is not None:
            from pathlib import Path

            if isinstance(path_data, Path):
                return path_data.read_bytes()

        url_data = getattr(self, "url", None)
        if isinstance(url_data, str):
            from zhenxun.utils.http_utils import AsyncHttpx

            content = await AsyncHttpx.get_content(url_data)
            if isinstance(content, bytes):
                return content
            return b""

        raise ValueError(
            f"{self.__class__.__name__} 未提供有效的数据源 (url, raw, path)"
        )

    async def get_base64_data(self) -> str:
        """统一获取 Base64 编码的字符串数据"""
        return base64.b64encode(await self.get_raw_bytes()).decode("utf-8")

    async def get_data_uri(self, default_mime: str = "application/octet-stream") -> str:
        """统一获取 Data URI (data:mime;base64,...) 格式的字符串"""
        mime = getattr(self, "mime_type", None) or default_mime
        return f"data:{mime};base64,{await self.get_base64_data()}"


class ImagePart(BaseContentPart):
    """图片媒体片段"""

    type: Literal["image"] = "image"
    url: str | None = None
    """外网图片 URL (需能直接访问)"""
    raw: bytes | None = None
    """图片二进制裸数据"""
    path: Path | None = None
    """本地文件系统中的图片路径"""
    mime_type: str | None = None
    """图片 MIME 类型 (如 image/jpeg)"""
    media_resolution: str | None = None
    """媒体处理强制分辨率策略"""

    @model_validator(mode="after")
    def _validate_source(self) -> Self:
        sources = [s for s in (self.url, self.raw, self.path) if s is not None]
        if len(sources) != 1:
            raise ValueError("ImagePart 必须且只能提供 url, raw, path 中的一个")
        return self


class AudioPart(BaseContentPart):
    """音频媒体片段"""

    type: Literal["audio"] = "audio"
    url: str | None = None
    """外网音频 URL"""
    raw: bytes | None = None
    """音频二进制裸数据"""
    path: Path | None = None
    """本地文件系统中的音频路径"""
    mime_type: str | None = None
    """音频 MIME 类型 (如 audio/mp3)"""

    @model_validator(mode="after")
    def _validate_source(self) -> Self:
        sources = [s for s in (self.url, self.raw, self.path) if s is not None]
        if len(sources) != 1:
            raise ValueError("AudioPart 必须且只能提供 url, raw, path 中的一个")
        return self


class VideoPart(BaseContentPart):
    """视频媒体片段"""

    type: Literal["video"] = "video"
    url: str | None = None
    """外网视频 URL"""
    raw: bytes | None = None
    """视频二进制裸数据"""
    path: Path | None = None
    """本地文件系统中的视频路径"""
    mime_type: str | None = None
    """视频 MIME 类型 (如 video/mp4)"""

    @model_validator(mode="after")
    def _validate_source(self) -> Self:
        sources = [s for s in (self.url, self.raw, self.path) if s is not None]
        if len(sources) != 1:
            raise ValueError("VideoPart 必须且只能提供 url, raw, path 中的一个")
        return self


class FilePart(BaseContentPart):
    """通用文件片段 (如 PDF、代码文件等)"""

    type: Literal["file"] = "file"
    url: str | None = None
    """外网文件 URL (或 Google Cloud gs:// URI)"""
    raw: bytes | None = None
    """文件二进制裸数据"""
    path: Path | None = None
    """本地文件系统中的文件路径"""
    mime_type: str | None = None
    """文件 MIME 类型 (如 application/pdf)"""

    @model_validator(mode="after")
    def _validate_source(self) -> Self:
        sources = [s for s in (self.url, self.raw, self.path) if s is not None]
        if len(sources) != 1:
            raise ValueError("FilePart 必须且只能提供 url, raw, path 中的一个")
        return self


class TextPart(BaseContentPart):
    """基础文本片段"""

    type: Literal["text"] = "text"
    text: str
    """具体的纯文本内容"""


class ThoughtPart(BaseContentPart):
    """思考链 (Chain of Thought) 片段，用于容纳模型的内部思维过程"""

    type: Literal["thought"] = "thought"
    thought_text: str
    """思考过程的文本"""


class ToolCallPart(BaseContentPart):
    """工具调用请求片段 (由模型发出)"""

    type: Literal["tool_call"] = "tool_call"
    id: str
    """工具调用唯一 ID"""
    tool_name: str
    """调用的函数名称"""
    args: dict[str, Any] | str
    """传递给工具的参数 (解析后的字典或原始 JSON 字符串)"""


class ToolReturnPart(BaseContentPart):
    """工具调用结果片段 (发给模型)"""

    type: Literal["tool_return"] = "tool_return"
    tool_call_id: str
    """关联的原始调用 ID"""
    tool_name: str
    """执行的工具名称"""
    output: Any
    """工具执行结果的有效载荷"""


class TextDeltaPart(BaseModel):
    """流式文本增量片段"""

    type: Literal["text_delta"] = "text_delta"
    content_delta: str
    """流式返回的文本增量字符串"""


class ThoughtDeltaPart(BaseModel):
    """流式思考链增量片段"""

    type: Literal["thought_delta"] = "thought_delta"
    content_delta: str
    """流式返回的思考过程增量字符串"""


class ToolCallDeltaPart(BaseModel):
    """流式工具调用增量片段"""

    type: Literal["tool_call_delta"] = "tool_call_delta"
    tool_call_id: str | None = None
    """(可选) 流式返回的工具调用唯一ID"""
    tool_name_delta: str | None = None
    """(可选) 流式返回的工具名称增量"""
    args_delta: str | None = None
    """(可选) 流式返回的 JSON 格式参数增量"""


LLMContentPart = Annotated[
    TextPart
    | ImagePart
    | AudioPart
    | VideoPart
    | FilePart
    | ThoughtPart
    | ToolCallPart
    | ToolReturnPart,
    Field(discriminator="type"),
]
"""大模型底层标准内容片段的 Annotated 联合类型"""


class EmbedPayload(BaseModel):
    """单一的嵌入载体，包含一个或多个多模态片段（融合向量）"""

    parts: list[LLMContentPart] = Field(default_factory=list)

    @property
    def text(self) -> str:
        """快速提取纯文本（用于向下兼容不支持多模态的模型）"""
        return "".join(p.text for p in self.parts if isinstance(p, TextPart)).strip()

    @property
    def has_multimodal(self) -> bool:
        """判断是否包含图像/音频/视频/文件等非文本模态"""
        return any(not isinstance(p, TextPart) for p in self.parts)


class EmbedBatch(BaseModel):
    """一次嵌入 API 请求的批次载体"""

    payloads: list[EmbedPayload] = Field(default_factory=list)

    def to_text_only(self, context_name: str) -> list[str]:
        """降级工具：将多模态的批量向量安全剔除图片等内容，回退为纯文本数组"""

        texts = []
        for payload in self.payloads:
            if payload.has_multimodal:
                logger.warning(
                    f"⚠️ 模型 {context_name} "
                    "不支持多模态嵌入，已自动剔除富媒体内容，静默降级为纯文本进行向量化..."
                )
            texts.append(payload.text if payload.text else " ")
        return texts


__all__ = [
    "AudioPart",
    "BaseContentPart",
    "EmbedBatch",
    "EmbedPayload",
    "FilePart",
    "ImagePart",
    "LLMContentPart",
    "TextDeltaPart",
    "TextPart",
    "ThoughtDeltaPart",
    "ThoughtPart",
    "ToolCallDeltaPart",
    "ToolCallPart",
    "ToolReturnPart",
    "VideoPart",
]
