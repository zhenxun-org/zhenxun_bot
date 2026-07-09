from __future__ import annotations

import json
from typing import Any

import httpx

from zhenxun.services.ai.core.exceptions import ResponseParseException
from zhenxun.services.ai.core.messages import (
    AssistantMessage,
    AudioResponse,
    LLMMessage,
    SpeechRequest,
    ThoughtPart,
)
from zhenxun.services.ai.core.models import (
    ModelCapabilities,
    ModelDetail,
    ModelIdentity,
)
from zhenxun.services.ai.core.options import GenerationConfig, TTSConfig

from .base import BaseAdapter, RequestData
from .handlers.base import BaseAudioHandler
from .handlers.openai_handlers import (
    OpenAIConfigMapper,
    OpenAIMessageConverter,
    OpenAITextHandler,
)
from .openai import OpenAICompatAdapter


class MiniMaxAudioHandler(BaseAudioHandler):
    """MiniMax 专有文本转语音处理器"""

    def prepare_speech_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: SpeechRequest,
    ) -> RequestData:
        input_text = request.input_text
        config = request.config or TTSConfig()

        config_voice = config.minimax_options.voice_id
        voice = (
            config_voice
            or request.voice
            or identity.capabilities.default_voice_id
            or "female-shaonv"
        )

        endpoint = "/v1/t2a_v2"
        base_url = (
            identity.api_base.rstrip("/")
            if identity.api_base
            else "https://api.minimaxi.com"
        )
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        url = f"{base_url}{endpoint}"

        headers = adapter.get_base_headers(api_key)

        voice_setting: dict[str, Any] = {"voice_id": voice}
        if config.speed != 1.0:
            voice_setting["speed"] = config.speed
        if config.minimax_options.vol is not None:
            voice_setting["vol"] = config.minimax_options.vol
        if config.minimax_options.pitch is not None:
            voice_setting["pitch"] = config.minimax_options.pitch
        if config.minimax_options.emotion is not None:
            voice_setting["emotion"] = config.minimax_options.emotion

        target_format = config.response_format
        if target_format not in ("mp3", "pcm", "flac", "wav"):
            target_format = "mp3"

        audio_setting = {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": target_format,
            "channel": 1,
        }

        body: dict[str, Any] = {
            "model": identity.model_name,
            "text": input_text,
            "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": audio_setting,
        }

        if config.minimax_options.timbre_weights:
            body["timbre_weights"] = config.minimax_options.timbre_weights
            body["voice_setting"]["voice_id"] = ""

        if config.minimax_options.pronunciation_dict:
            body["pronunciation_dict"] = config.minimax_options.pronunciation_dict

        return RequestData(url=url, headers=headers, body=body)

    async def parse_speech_response(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        raw_response: httpx.Response,
    ) -> AudioResponse:
        resp_bytes = await raw_response.aread()
        data = json.loads(resp_bytes)

        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code", 0) != 0:
            raise ResponseParseException(
                f"MiniMax 语音合成失败: {base_resp.get('status_msg')}",
                details=data,
            )

        try:
            audio_hex = data["data"]["audio"]
            audio_bytes = bytes.fromhex(audio_hex)
        except (KeyError, ValueError) as e:
            raise ResponseParseException(
                f"解析 MiniMax 语音 Hex 数据失败: {e}", details=data
            )

        extra_info = data.get("extra_info", {})
        audio_format = extra_info.get("audio_format", "mp3")
        usage_chars = extra_info.get("usage_characters", 0)

        from zhenxun.services.ai.core.messages import AudioResponse, UsageInfo

        usage = UsageInfo()
        usage.prompt_tokens = usage_chars

        return AudioResponse(
            audio_bytes=audio_bytes,
            audio_format=audio_format,
            usage=usage,
            model_name=identity.model_name,
            raw_response=data,
        )


class MiniMaxMessageConverter(OpenAIMessageConverter):
    """MiniMax 消息转换器，处理特有的 reasoning_details 格式回传"""

    async def convert_messages_async(
        self, messages: list[LLMMessage]
    ) -> list[dict[str, Any]]:
        openai_messages = await super().convert_messages_async(messages)

        assistant_msgs = [m for m in messages if isinstance(m, AssistantMessage)]
        ast_idx = 0

        for o_msg in openai_messages:
            if o_msg.get("role") == "assistant":
                if ast_idx < len(assistant_msgs):
                    orig_ast = assistant_msgs[ast_idx]
                    ast_idx += 1

                    if "reasoning_content" in o_msg:
                        del o_msg["reasoning_content"]

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
                        else:
                            o_msg["reasoning_details"] = [
                                {"type": "reasoning.text", "text": part.thought_text}
                            ]

        return openai_messages


class MiniMaxConfigMapper(OpenAIConfigMapper):
    """MiniMax 专属配置映射器"""

    def map_config(
        self,
        config: GenerationConfig,
        model_detail: ModelDetail | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> dict[str, Any]:
        params = super().map_config(config, model_detail, capabilities)
        params["reasoning_split"] = True
        return params


class MiniMaxTextHandler(OpenAITextHandler):
    """MiniMax 复合文本处理器"""

    def __init__(self, api_type: str = "minimax"):
        super().__init__(api_type=api_type)
        self.converter = MiniMaxMessageConverter(api_type=api_type)
        self.mapper = MiniMaxConfigMapper(api_type=api_type)


class MiniMaxAdapter(OpenAICompatAdapter):
    """
    MiniMax API 适配器。
    """

    def __init__(self):
        """初始化 MiniMax 适配器并挂载专属处理器。"""
        super().__init__()
        self.text_handler = MiniMaxTextHandler(api_type=self.api_type)
        self.audio_handler = MiniMaxAudioHandler()

    @property
    def api_type(self) -> str:
        """适配器主类型标识。"""
        return "minimax"

    @property
    def supported_api_types(self) -> list[str]:
        """当前适配器支持的 API 类型列表。"""
        return ["minimax"]

    def get_chat_endpoint(self, identity: ModelIdentity) -> str:
        """根据官方兼容要求，重写获取端点，允许自定义覆盖"""
        return "/v1/chat/completions"

    def _get_base_url(self, identity: ModelIdentity) -> str:
        base_url = (
            identity.api_base.rstrip("/")
            if identity.api_base
            else "https://api.minimaxi.com"
        )
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        return base_url
