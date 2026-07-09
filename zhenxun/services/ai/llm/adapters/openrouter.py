import base64
from pathlib import Path
from typing import Any

from zhenxun.services.ai.core.exceptions import LLMException
from zhenxun.services.ai.core.messages import (
    ImagePart,
    ImageRequest,
    LLMMessage,
    ThoughtPart,
)
from zhenxun.services.ai.core.models import ModelIdentity

from .base import (
    BaseAdapter,
    RequestData,
    ResponseData,
    process_image_data,
)
from .handlers.base import BaseImageHandler
from .handlers.openai_handlers import (
    OpenAIMessageConverter,
    OpenAITextHandler,
)
from .openai import OpenAIAdapter


class OpenRouterMessageConverter(OpenAIMessageConverter):
    """OpenRouter 专有消息转换器：处理 reasoning_details 的无损回传"""

    async def convert_messages_async(
        self, messages: list[LLMMessage]
    ) -> list[dict[str, Any]]:
        openai_messages = await super().convert_messages_async(messages)

        assistant_msgs = [m for m in messages if getattr(m, "role", "") == "assistant"]
        ast_idx = 0

        for o_msg in openai_messages:
            if o_msg.get("role") == "assistant":
                if ast_idx < len(assistant_msgs):
                    orig_ast = assistant_msgs[ast_idx]
                    ast_idx += 1

                    thought_parts = [
                        p for p in orig_ast.content if isinstance(p, ThoughtPart)
                    ]
                    if thought_parts:
                        part = thought_parts[0]
                        raw_details = (
                            part.metadata.get("raw_reasoning_details")
                            if part.metadata
                            else None
                        )

                        if raw_details:
                            o_msg["reasoning_details"] = raw_details
                            o_msg.pop("reasoning_content", None)
                            o_msg.pop("reasoning", None)

        return openai_messages


class OpenRouterTextHandler(OpenAITextHandler):
    """OpenRouter 专有文本处理器，挂载专有 Converter"""

    def __init__(self, api_type: str = "openrouter"):
        super().__init__(api_type=api_type)
        self.converter = OpenRouterMessageConverter(api_type=api_type)


class OpenRouterImageHandler(BaseImageHandler):
    """OpenRouter 专有的图像生成处理器"""

    def prepare_image_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: ImageRequest,
    ) -> RequestData:
        headers = adapter.get_base_headers(api_key)

        endpoint = "/v1/chat/completions"
        url = adapter.get_api_url(identity, endpoint)

        body: dict[str, Any] = {
            "model": identity.model_name,
            "modalities": ["image", "text"],
        }

        if request.images:
            content_list: list[dict[str, Any]] = [
                {"type": "text", "text": request.prompt}
            ]
            for img_source in request.images:
                img_bytes = None
                if isinstance(img_source, bytes):
                    img_bytes = img_source
                elif hasattr(img_source, "read_bytes"):
                    img_bytes = img_source.read_bytes()
                elif isinstance(img_source, str) and img_source.startswith(
                    "data:image"
                ):
                    content_list.append(
                        {"type": "image_url", "image_url": {"url": img_source}}
                    )
                    continue
                else:
                    raise LLMException(
                        "OpenRouter 图像生成仅支持 bytes/Path/base64 URI"
                    )

                if img_bytes:
                    mime_type = "image/jpeg"
                    if img_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                        mime_type = "image/png"
                    elif img_bytes.startswith(b"GIF87a") or img_bytes.startswith(
                        b"GIF89a"
                    ):
                        mime_type = "image/gif"
                    elif img_bytes.startswith(b"RIFF") and img_bytes[8:12] == b"WEBP":
                        mime_type = "image/webp"

                    b64_str = base64.b64encode(img_bytes).decode("utf-8")
                    content_list.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64_str}"},
                        }
                    )
            body["messages"] = [{"role": "user", "content": content_list}]
        else:
            body["messages"] = [{"role": "user", "content": request.prompt}]

        if request.config:
            image_config = {}
            if request.config.media.aspect_ratio:
                image_config["aspect_ratio"] = str(request.config.media.aspect_ratio)
            if request.config.media.resolution:
                image_config["image_size"] = str(
                    request.config.media.resolution
                ).upper()

            if image_config:
                body["image_config"] = image_config

        return RequestData(url=url, headers=headers, body=body)

    def parse_image_response(
        self, adapter: BaseAdapter, response_json: dict[str, Any]
    ) -> ResponseData:
        adapter.validate_response(response_json)

        images_data = []
        choices = response_json.get("choices", [])

        if choices:
            message = choices[0].get("message", {})
            if "images" in message:
                for img_data in message["images"]:
                    img_url_obj = img_data.get("image_url", {})
                    url_str = img_url_obj.get("url", "")
                    if url_str.startswith("data:image"):
                        try:
                            b64_data = url_str.split(",", 1)[1]
                            decoded = base64.b64decode(b64_data)
                            images_data.append(process_image_data(decoded))
                        except Exception:
                            pass
                    elif url_str:
                        images_data.append(url_str)

        content_parts = []
        for img in images_data:
            if isinstance(img, str) and img.startswith("http"):
                content_parts.append(ImagePart(url=img))
            elif isinstance(img, bytes):
                content_parts.append(ImagePart(raw=img))
            else:
                content_parts.append(ImagePart(path=Path(img)))

        if not content_parts:
            raise LLMException("OpenRouter 图像生成响应中未找到有效的图片数据")

        return ResponseData(content_parts=content_parts, raw_response=response_json)


class OpenRouterAdapter(OpenAIAdapter):
    """OpenRouter 平台适配器"""

    def __init__(self):
        super().__init__()
        self.text_handler = OpenRouterTextHandler(api_type=self.api_type)
        self.image_handler = OpenRouterImageHandler()

    @property
    def api_type(self) -> str:
        return "openrouter"

    @property
    def supported_api_types(self) -> list[str]:
        return ["openrouter"]
