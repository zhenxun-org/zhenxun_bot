"""
LLM 内容类型定义

包含多模态内容部分、消息和响应的数据模型。
"""

import base64
import mimetypes
from pathlib import Path
from typing import Any

import aiofiles
from pydantic import BaseModel

from zhenxun.services.log import logger


class LLMContentPart(BaseModel):
    """LLM 消息内容部分 - 支持多模态内容"""

    type: str
    text: str | None = None
    image_source: str | None = None
    audio_source: str | None = None
    video_source: str | None = None
    document_source: str | None = None
    file_uri: str | None = None
    file_source: str | None = None
    url: str | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] | None = None

    def model_post_init(self, /, __context: Any) -> None:
        """验证内容部分的有效性"""
        _ = __context
        validation_rules = {
            "text": lambda: self.text,
            "image": lambda: self.image_source,
            "audio": lambda: self.audio_source,
            "video": lambda: self.video_source,
            "document": lambda: self.document_source,
            "file": lambda: self.file_uri or self.file_source,
            "url": lambda: self.url,
        }

        if self.type in validation_rules:
            if not validation_rules[self.type]():
                raise ValueError(f"{self.type}类型的内容部分必须包含相应字段")

    @classmethod
    def text_part(cls, text: str) -> "LLMContentPart":
        """创建文本内容部分"""
        return cls(type="text", text=text)

    @classmethod
    def image_url_part(cls, url: str) -> "LLMContentPart":
        """创建图片URL内容部分"""
        return cls(type="image", image_source=url)

    @classmethod
    def image_base64_part(
        cls, data: str, mime_type: str = "image/png"
    ) -> "LLMContentPart":
        """创建Base64图片内容部分"""
        data_url = f"data:{mime_type};base64,{data}"
        return cls(type="image", image_source=data_url)

    @classmethod
    def audio_url_part(cls, url: str, mime_type: str = "audio/wav") -> "LLMContentPart":
        """创建音频URL内容部分"""
        return cls(type="audio", audio_source=url, mime_type=mime_type)

    @classmethod
    def video_url_part(cls, url: str, mime_type: str = "video/mp4") -> "LLMContentPart":
        """创建视频URL内容部分"""
        return cls(type="video", video_source=url, mime_type=mime_type)

    @classmethod
    def video_base64_part(
        cls, data: str, mime_type: str = "video/mp4"
    ) -> "LLMContentPart":
        """创建Base64视频内容部分"""
        data_url = f"data:{mime_type};base64,{data}"
        return cls(type="video", video_source=data_url, mime_type=mime_type)

    @classmethod
    def audio_base64_part(
        cls, data: str, mime_type: str = "audio/wav"
    ) -> "LLMContentPart":
        """创建Base64音频内容部分"""
        data_url = f"data:{mime_type};base64,{data}"
        return cls(type="audio", audio_source=data_url, mime_type=mime_type)

    @classmethod
    def file_uri_part(
        cls,
        file_uri: str,
        mime_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "LLMContentPart":
        """创建Gemini File API URI内容部分"""
        return cls(
            type="file",
            file_uri=file_uri,
            mime_type=mime_type,
            metadata=metadata or {},
        )

    @classmethod
    async def from_path(
        cls, path_like: str | Path, target_api: str | None = None
    ) -> "LLMContentPart | None":
        """
        从本地文件路径创建 LLMContentPart。
        自动检测MIME类型，并根据类型（如图片）可能加载为Base64。
        target_api 可以用于提示如何最好地准备数据（例如 'gemini' 可能偏好 base64）
        """
        try:
            path = Path(path_like)
            if not path.exists() or not path.is_file():
                logger.warning(f"文件不存在或不是一个文件: {path}")
                return None

            mime_type, _ = mimetypes.guess_type(path.resolve().as_uri())

            if not mime_type:
                logger.warning(
                    f"无法猜测文件 {path.name} 的MIME类型，将尝试作为文本文件处理。"
                )
                try:
                    async with aiofiles.open(path, encoding="utf-8") as f:
                        text_content = await f.read()
                    return cls.text_part(text_content)
                except Exception as e:
                    logger.error(f"读取文本文件 {path.name} 失败: {e}")
                    return None

            if mime_type.startswith("image/"):
                if target_api == "gemini" or not path.is_absolute():
                    try:
                        async with aiofiles.open(path, "rb") as f:
                            img_bytes = await f.read()
                        base64_data = base64.b64encode(img_bytes).decode("utf-8")
                        return cls.image_base64_part(
                            data=base64_data, mime_type=mime_type
                        )
                    except Exception as e:
                        logger.error(f"读取或编码图片文件 {path.name} 失败: {e}")
                        return None
                else:
                    logger.warning(
                        f"为本地图片路径 {path.name} 生成 image_url_part。"
                        "实际API可能不支持 file:// URI。考虑使用Base64或公网URL。"
                    )
                    return cls.image_url_part(url=path.resolve().as_uri())
            elif mime_type.startswith("audio/"):
                return cls.audio_url_part(
                    url=path.resolve().as_uri(), mime_type=mime_type
                )
            elif mime_type.startswith("video/"):
                if target_api == "gemini":
                    # 对于 Gemini API，将视频转换为 base64
                    try:
                        async with aiofiles.open(path, "rb") as f:
                            video_bytes = await f.read()
                        base64_data = base64.b64encode(video_bytes).decode("utf-8")
                        return cls.video_base64_part(
                            data=base64_data, mime_type=mime_type
                        )
                    except Exception as e:
                        logger.error(f"读取或编码视频文件 {path.name} 失败: {e}")
                        return None
                else:
                    return cls.video_url_part(
                        url=path.resolve().as_uri(), mime_type=mime_type
                    )
            elif (
                mime_type.startswith("text/")
                or mime_type == "application/json"
                or mime_type == "application/xml"
            ):
                try:
                    async with aiofiles.open(path, encoding="utf-8") as f:
                        text_content = await f.read()
                    return cls.text_part(text_content)
                except Exception as e:
                    logger.error(f"读取文本类文件 {path.name} 失败: {e}")
                    return None
            else:
                logger.info(
                    f"文件 {path.name} (MIME: {mime_type}) 将作为通用文件URI处理。"
                )
                return cls.file_uri_part(
                    file_uri=path.resolve().as_uri(),
                    mime_type=mime_type,
                    metadata={"name": path.name, "source": "local_path"},
                )

        except Exception as e:
            logger.error(f"从路径 {path_like} 创建LLMContentPart时出错: {e}")
            return None

    def is_image_url(self) -> bool:
        """检查图像源是否为URL"""
        if not self.image_source:
            return False
        return self.image_source.startswith(("http://", "https://"))

    def is_image_base64(self) -> bool:
        """检查图像源是否为Base64 Data URL"""
        if not self.image_source:
            return False
        return self.image_source.startswith("data:")

    def get_base64_data(self) -> tuple[str, str] | None:
        """从Data URL中提取Base64数据和MIME类型"""
        if not self.is_image_base64() or not self.image_source:
            return None

        try:
            header, data = self.image_source.split(",", 1)
            mime_part = header.split(";")[0].replace("data:", "")
            return mime_part, data
        except (ValueError, IndexError):
            logger.warning(f"无法解析Base64图像数据: {self.image_source[:50]}...")
            return None

    async def convert_for_api_async(self, api_type: str) -> dict[str, Any]:
        """根据API类型转换多模态内容格式"""
        from zhenxun.utils.http_utils import AsyncHttpx

        if self.type == "text":
            if api_type == "openai":
                return {"type": "text", "text": self.text}
            elif api_type == "gemini":
                return {"text": self.text}
            else:
                return {"type": "text", "text": self.text}

        elif self.type == "image":
            if not self.image_source:
                raise ValueError("图像类型的内容必须包含image_source")

            if api_type == "openai":
                return {"type": "image_url", "image_url": {"url": self.image_source}}
            elif api_type == "gemini":
                if self.is_image_base64():
                    base64_info = self.get_base64_data()
                    if base64_info:
                        mime_type, data = base64_info
                        return {"inlineData": {"mimeType": mime_type, "data": data}}
                    else:
                        raise ValueError(
                            f"无法解析Base64图像数据: {self.image_source[:50]}..."
                        )
                elif self.is_image_url():
                    logger.debug(f"正在为Gemini下载并编码URL图片: {self.image_source}")
                    try:
                        image_bytes = await AsyncHttpx.get_content(self.image_source)
                        mime_type = self.mime_type or "image/jpeg"
                        base64_data = base64.b64encode(image_bytes).decode("utf-8")
                        return {
                            "inlineData": {"mimeType": mime_type, "data": base64_data}
                        }
                    except Exception as e:
                        logger.error(f"下载或编码URL图片失败: {e}", e=e)
                        raise ValueError(f"无法处理图片URL: {e}")
                else:
                    raise ValueError(f"不支持的图像源格式: {self.image_source[:50]}...")
            else:
                return {"type": "image_url", "image_url": {"url": self.image_source}}

        elif self.type == "video":
            if not self.video_source:
                raise ValueError("视频类型的内容必须包含video_source")

            if api_type == "gemini":
                # Gemini 支持视频，但需要通过 File API 上传
                if self.video_source.startswith("data:"):
                    # 处理 base64 视频数据
                    try:
                        header, data = self.video_source.split(",", 1)
                        mime_type = header.split(";")[0].replace("data:", "")
                        return {"inlineData": {"mimeType": mime_type, "data": data}}
                    except (ValueError, IndexError):
                        raise ValueError(
                            f"无法解析Base64视频数据: {self.video_source[:50]}..."
                        )
                else:
                    # 对于 URL 或其他格式，暂时不支持直接内联
                    raise ValueError(
                        "Gemini API 的视频处理需要通过 File API 上传，不支持直接 URL"
                    )
            else:
                # 其他 API 可能不支持视频
                raise ValueError(f"API类型 '{api_type}' 不支持视频内容")

        elif self.type == "audio":
            if not self.audio_source:
                raise ValueError("音频类型的内容必须包含audio_source")

            if api_type == "gemini":
                # Gemini 支持音频，处理方式类似视频
                if self.audio_source.startswith("data:"):
                    try:
                        header, data = self.audio_source.split(",", 1)
                        mime_type = header.split(";")[0].replace("data:", "")
                        return {"inlineData": {"mimeType": mime_type, "data": data}}
                    except (ValueError, IndexError):
                        raise ValueError(
                            f"无法解析Base64音频数据: {self.audio_source[:50]}..."
                        )
                else:
                    raise ValueError(
                        "Gemini API 的音频处理需要通过 File API 上传，不支持直接 URL"
                    )
            else:
                raise ValueError(f"API类型 '{api_type}' 不支持音频内容")

        elif self.type == "file":
            if api_type == "gemini" and self.file_uri:
                return {
                    "fileData": {"mimeType": self.mime_type, "fileUri": self.file_uri}
                }
            elif self.file_source:
                file_name = (
                    self.metadata.get("name", "file") if self.metadata else "file"
                )
                if api_type == "gemini":
                    return {"text": f"[文件: {file_name}]\n{self.file_source}"}
                else:
                    return {
                        "type": "text",
                        "text": f"[文件: {file_name}]\n{self.file_source}",
                    }
            else:
                raise ValueError("文件类型的内容必须包含file_uri或file_source")

        else:
            raise ValueError(f"不支持的内容类型: {self.type}")


class LLMMessage(BaseModel):
    """LLM 消息"""

    role: str
    content: str | list[LLMContentPart]
    name: str | None = None
    tool_calls: list[Any] | None = None
    tool_call_id: str | None = None

    def model_post_init(self, /, __context: Any) -> None:
        """验证消息的有效性"""
        _ = __context
        if self.role == "tool":
            if not self.tool_call_id:
                raise ValueError("工具角色的消息必须包含 tool_call_id")
            if not self.name:
                raise ValueError("工具角色的消息必须包含函数名 (在 name 字段中)")
        if self.role == "tool" and not isinstance(self.content, str):
            logger.warning(
                f"工具角色消息的内容期望是字符串，但得到的是: {type(self.content)}. "
                "将尝试转换为字符串。"
            )
            try:
                self.content = str(self.content)
            except Exception as e:
                raise ValueError(f"无法将工具角色的内容转换为字符串: {e}")

    @classmethod
    def user(cls, content: str | list[LLMContentPart]) -> "LLMMessage":
        """创建用户消息"""
        return cls(role="user", content=content)

    @classmethod
    def assistant_tool_calls(
        cls,
        tool_calls: list[Any],
        content: str | list[LLMContentPart] = "",
    ) -> "LLMMessage":
        """创建助手请求工具调用的消息"""
        return cls(role="assistant", content=content, tool_calls=tool_calls)

    @classmethod
    def assistant_text_response(
        cls, content: str | list[LLMContentPart]
    ) -> "LLMMessage":
        """创建助手纯文本回复的消息"""
        return cls(role="assistant", content=content, tool_calls=None)

    @classmethod
    def tool_response(
        cls,
        tool_call_id: str,
        function_name: str,
        result: Any,
    ) -> "LLMMessage":
        """创建工具执行结果的消息"""
        import json

        try:
            content_str = json.dumps(result)
        except TypeError as e:
            logger.error(
                f"工具 '{function_name}' 的结果无法JSON序列化: {result}. 错误: {e}"
            )
            content_str = json.dumps(
                {"error": "Tool result not JSON serializable", "details": str(e)}
            )

        return cls(
            role="tool",
            content=content_str,
            tool_call_id=tool_call_id,
            name=function_name,
        )

    @classmethod
    def system(cls, content: str) -> "LLMMessage":
        """创建系统消息"""
        return cls(role="system", content=content)


class LLMResponse(BaseModel):
    """LLM 响应"""

    text: str
    usage_info: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    tool_calls: list[Any] | None = None
    code_executions: list[Any] | None = None
    grounding_metadata: Any | None = None
    cache_info: Any | None = None
