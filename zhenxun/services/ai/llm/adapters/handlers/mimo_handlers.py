import base64
import json
from typing import Any

import httpx

from zhenxun.services.ai.core.messages import (
    AudioPart,
    AudioResponse,
    ImagePart,
    LLMMessage,
    SpeechRequest,
    TextPart,
    UsageInfo,
    VideoPart,
)
from zhenxun.services.ai.core.models import (
    ModelCapabilities,
    ModelIdentity,
)
from zhenxun.services.ai.core.options import TTSConfig
from zhenxun.services.ai.llm.adapters.base import BaseAdapter, RequestData

from .base import BaseAudioHandler
from .openai_handlers import (
    OpenAIConfigMapper,
    OpenAIMessageConverter,
    OpenAITextHandler,
    OpenAIToolSerializer,
)


class MiMoToolSerializer(OpenAIToolSerializer):
    """MiMo 工具序列化器，负责拦截并构造独有的 web_search 工具"""

    def serialize_server_tools(
        self, tools: list[Any], capabilities: ModelCapabilities
    ) -> list[dict[str, Any]]:
        res = []
        for t in tools:
            type_id = getattr(t, "type_id", "unknown")
            if type_id not in capabilities.supported_native_tools:
                continue
            if type_id == "web_search":
                res.append(
                    {
                        "type": "web_search",
                        "max_keyword": getattr(t, "max_keyword", 3),
                        "force_search": getattr(t, "force_search", True),
                        "limit": getattr(t, "limit", 1),
                    }
                )
        return res


class MiMoMessageConverter(OpenAIMessageConverter):
    """MiMo 消息转换器，拦截处理特有的音视频多模态结构"""

    async def convert_messages_async(
        self, messages: list[LLMMessage]
    ) -> list[dict[str, Any]]:
        openai_messages = await super().convert_messages_async(messages)

        for o_msg, o_orig in zip(openai_messages, messages):
            if o_msg["role"] == "user":
                content_parts = []
                for part in o_orig.content:
                    if isinstance(part, TextPart):
                        content_parts.append({"type": "text", "text": part.text})
                    elif isinstance(part, ImagePart):
                        src = (
                            part.url
                            if part.url
                            else await part.get_data_uri(part.mime_type or "image/jpeg")
                        )
                        content_parts.append(
                            {"type": "image_url", "image_url": {"url": src}}
                        )
                    elif isinstance(part, VideoPart):
                        src = (
                            part.url
                            if part.url
                            else await part.get_data_uri(part.mime_type or "video/mp4")
                        )
                        content_parts.append(
                            {
                                "type": "video_url",
                                "video_url": {"url": src},
                                "fps": getattr(part, "fps", 2),
                                "media_resolution": getattr(
                                    part, "media_resolution", "default"
                                ),
                            }
                        )
                    elif isinstance(part, AudioPart):
                        src = (
                            part.url
                            if part.url
                            else await part.get_data_uri(part.mime_type or "audio/mp3")
                        )
                        content_parts.append(
                            {"type": "input_audio", "input_audio": {"data": src}}
                        )
                o_msg["content"] = content_parts
        return openai_messages


class MiMoTextHandler(OpenAITextHandler):
    """MiMo 文本对话处理器集成"""

    def __init__(self, api_type: str = "mimo"):
        super().__init__(api_type=api_type)
        self.converter = MiMoMessageConverter(api_type=api_type)
        self.serializer = MiMoToolSerializer(api_type=api_type)
        self.mapper = OpenAIConfigMapper(api_type=api_type)


class MiMoAudioHandler(BaseAudioHandler):
    """MiMo TTS 接口实现：挂载在 Chat Completions 端点上"""

    def prepare_speech_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: SpeechRequest,
    ) -> RequestData:
        input_text = request.input_text
        config = request.config or TTSConfig()

        config_voice = (
            config.mimo_options.voice_id if hasattr(config, "mimo_options") else None
        )
        voice = (
            config_voice
            or request.voice
            or identity.capabilities.default_voice_id
            or "mimo_default"
        )

        url = adapter.get_api_url(identity, "/v1/chat/completions")
        headers = adapter.get_base_headers(api_key)

        body = {
            "model": identity.model_name,
            "messages": [{"role": "assistant", "content": input_text}],
            "audio": {
                "format": config.response_format
                if config.response_format in ("wav", "pcm16")
                else "wav",
                "voice": voice,
            },
        }
        return RequestData(url=url, headers=headers, body=body)

    async def parse_speech_response(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        raw_response: httpx.Response,
    ) -> AudioResponse:
        data = json.loads(await raw_response.aread())
        adapter.validate_response(data)
        audio_b64 = data["choices"][0]["message"]["audio"]["data"]
        return AudioResponse(
            audio_bytes=base64.b64decode(audio_b64),
            audio_format="wav",
            usage=UsageInfo(),
            model_name=identity.model_name,
        )
