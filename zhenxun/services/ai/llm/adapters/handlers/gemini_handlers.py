import base64
import io
import json
from typing import Any
import uuid
import wave

import httpx

from zhenxun.services.ai.config import get_gemini_safety_threshold
from zhenxun.services.ai.core.exceptions import (
    ContentFilteredException,
    InvalidRequestException,
    QuotaExceededException,
    RateLimitException,
    ResponseParseException,
)
from zhenxun.services.ai.core.messages import (
    AssistantMessage,
    AudioPart,
    AudioResponse,
    ChatRequest,
    EmbeddingRequest,
    FilePart,
    ImagePart,
    ImageRequest,
    LLMContentPart,
    LLMGroundingAttribution,
    LLMGroundingMetadata,
    LLMMessage,
    SpeechRequest,
    SystemMessage,
    TextPart,
    ThoughtPart,
    ToolCallPart,
    ToolMessage,
    ToolReturnPart,
    UserMessage,
    VideoPart,
)
from zhenxun.services.ai.core.models import (
    ModelCapabilities,
    ModelDetail,
    ModelIdentity,
    ReasoningMode,
)
from zhenxun.services.ai.core.options import (
    GenerationConfig,
    LLMEmbeddingConfig,
    ResponseFormat,
    TTSConfig,
)
from zhenxun.services.ai.llm.adapters.base import (
    BaseAdapter,
    RequestData,
    ResponseData,
    process_image_data,
)
from zhenxun.services.ai.utils.logger import log_llm as logger

from .base import (
    BaseAudioHandler,
    BaseEmbeddingHandler,
    BaseImageHandler,
    BaseTextHandler,
    ConfigMapper,
    MessageConverter,
    ResponseParser,
    ToolSerializer,
)


class GeminiConfigMapper(ConfigMapper):
    def map_config(
        self,
        config: GenerationConfig,
        model_detail: ModelDetail | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}

        if config.common:
            if config.common.temperature is not None:
                params["temperature"] = config.common.temperature
            if config.common.max_tokens is not None:
                params["maxOutputTokens"] = config.common.max_tokens
            if config.common.top_k is not None:
                params["topK"] = config.common.top_k
            if config.common.top_p is not None:
                params["topP"] = config.common.top_p
            if config.common.stop is not None:
                params["stopSequences"] = (
                    config.common.stop
                    if isinstance(config.common.stop, list)
                    else [config.common.stop]
                )

        if (
            config.output.response_format == ResponseFormat.JSON
            or config.output.response_mime_type == "application/json"
        ):
            params["responseMimeType"] = "application/json"
            if config.output.response_schema:
                serializer = GeminiToolSerializer()
                params["responseJsonSchema"] = serializer.sanitize_schema(
                    config.output.response_schema
                )
        elif config.output.response_mime_type:
            params["responseMimeType"] = config.output.response_mime_type
        if config.output.response_modalities:
            params["responseModalities"] = config.output.response_modalities

        if config.tools.mode:
            fc_config: dict[str, Any] = {"mode": config.tools.mode}
            if config.tools.allowed_function_names and config.tools.mode == "ANY":
                user_funcs = [
                    name
                    for name in config.tools.allowed_function_names
                    if name not in {"code_execution", "google_search", "google_map"}
                ]
                if user_funcs:
                    fc_config["allowedFunctionNames"] = user_funcs
            params["toolConfig"] = {"functionCallingConfig": fc_config}

        if config.common.reasoning_effort and capabilities:
            effort = str(config.common.reasoning_effort).lower()
            if capabilities.reasoning_effort_map:
                effort = capabilities.reasoning_effort_map.get(effort, effort)

            if capabilities.reasoning_mode == ReasoningMode.LEVEL and effort != "none":
                thinking_config = params.setdefault("thinkingConfig", {})
                thinking_config["thinkingLevel"] = effort
            elif (
                capabilities.reasoning_mode == ReasoningMode.BUDGET and effort == "none"
            ):
                thinking_config = params.setdefault("thinkingConfig", {})
                thinking_config["thinkingBudget"] = 0

        if config.gemini_options.include_thoughts is not None:
            thinking_config = params.setdefault("thinkingConfig", {})
            thinking_config["includeThoughts"] = config.gemini_options.include_thoughts
        elif capabilities and capabilities.reasoning_visibility == "visible":
            thinking_config = params.setdefault("thinkingConfig", {})
            thinking_config["includeThoughts"] = True

        if "thinkingConfig" in params and not params["thinkingConfig"]:
            params.pop("thinkingConfig", None)

        image_config: dict[str, Any] = {}

        if config.media.aspect_ratio is not None:
            image_config["aspectRatio"] = config.media.aspect_ratio

        if config.media.resolution is not None:
            res_str = str(config.media.resolution).upper()
            if "1024" in res_str:
                res_str = "1K"
            elif "1536" in res_str or "2048" in res_str:
                res_str = "2K"
            elif "4096" in res_str:
                res_str = "4K"
            image_config["imageSize"] = res_str

        if image_config:
            params["imageConfig"] = image_config

        if config.media.quality:
            quality_map = {
                "low": "LOW",
                "medium": "MEDIUM",
                "high": "HIGH",
                "standard": "MEDIUM",
                "hd": "HIGH",
            }
            mapped_quality = quality_map.get(config.media.quality, "HIGH")
            params["mediaResolution"] = f"MEDIA_RESOLUTION_{mapped_quality}"

        if config.custom_kwargs:
            mapped_custom = config.custom_kwargs.copy()
            for key in ("code_execution_timeout", "reflexion_retries", "__cache_ttl__"):
                mapped_custom.pop(key, None)

            for unsupported in [
                "frequency_penalty",
                "presence_penalty",
                "repetition_penalty",
            ]:
                if unsupported in mapped_custom:
                    mapped_custom.pop(unsupported)

            params.update(mapped_custom)

        safety_settings: list[dict[str, Any]] = []
        if config.gemini_options.safety_settings:
            for category, threshold in config.gemini_options.safety_settings.items():
                safety_settings.append({"category": category, "threshold": threshold})
        else:
            threshold = get_gemini_safety_threshold()
            for category in [
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            ]:
                safety_settings.append({"category": category, "threshold": threshold})

        if safety_settings:
            params["safetySettings"] = safety_settings

        return params


class GeminiMessageConverter(MessageConverter):
    async def convert_part(self, part: LLMContentPart) -> dict[str, Any] | None:
        """将单个内容部分转换为 Gemini API 格式"""

        def _get_gemini_resolution_dict() -> dict[str, Any]:
            res_val = getattr(part, "media_resolution", None)
            if res_val and isinstance(res_val, str):
                value = res_val.upper()
                if not value.startswith("MEDIA_RESOLUTION_"):
                    value = f"MEDIA_RESOLUTION_{value}"
                return {"media_resolution": {"level": value}}
            return {}

        if isinstance(part, TextPart):
            return {"text": part.text}

        if isinstance(part, ThoughtPart):
            return {"text": part.thought_text, "thought": True}

        if isinstance(part, ImagePart):
            payload = {
                "inlineData": {
                    "mimeType": part.mime_type or "image/jpeg",
                    "data": await part.get_base64_data(),
                }
            }
            payload.update(_get_gemini_resolution_dict())
            return payload

        if isinstance(part, VideoPart):
            payload = {
                "inlineData": {
                    "mimeType": part.mime_type or "video/mp4",
                    "data": await part.get_base64_data(),
                }
            }
            payload.update(_get_gemini_resolution_dict())
            return payload

        if isinstance(part, AudioPart):
            payload = {
                "inlineData": {
                    "mimeType": part.mime_type or "audio/mp3",
                    "data": await part.get_base64_data(),
                }
            }
            payload.update(_get_gemini_resolution_dict())
            return payload

        if isinstance(part, FilePart):
            payload = {
                "inlineData": {
                    "mimeType": part.mime_type or "application/octet-stream",
                    "data": await part.get_base64_data(),
                }
            }
            payload.update(_get_gemini_resolution_dict())
            return payload

        if isinstance(part, ToolCallPart):
            func_call = {
                "name": part.tool_name,
                "args": part.args
                if isinstance(part.args, dict)
                else (json.loads(part.args) if part.args else {}),
            }
            if part.id and part.id != "unknown":
                func_call["id"] = part.id

            payload = {"functionCall": func_call}
            if part.metadata and "thought_signature" in part.metadata:
                payload["thoughtSignature"] = part.metadata["thought_signature"]
            return payload

        if isinstance(part, ToolReturnPart):
            func_resp = {
                "name": part.tool_name,
                "response": part.output
                if isinstance(part.output, dict)
                else {"result": part.output},
            }
            if part.tool_call_id and part.tool_call_id != "unknown":
                func_resp["id"] = part.tool_call_id

            payload = {"functionResponse": func_resp}
            return payload

        raise ValueError(f"不支持的内容类型: {part.type}")

    async def convert_messages_async(
        self, messages: list[LLMMessage]
    ) -> list[dict[str, Any]]:
        gemini_contents: list[dict[str, Any]] = []

        for msg in messages:
            current_parts: list[dict[str, Any]] = []
            if isinstance(msg, SystemMessage):
                continue

            elif isinstance(msg, UserMessage):
                for part_obj in msg.content:
                    part_dict = await self.convert_part(part_obj)
                    if part_dict is not None:
                        current_parts.append(part_dict)
                gemini_contents.append({"role": "user", "parts": current_parts})

            elif isinstance(msg, AssistantMessage):
                for part_obj in msg.content:
                    part_dict = await self.convert_part(part_obj)
                    if part_dict is None:
                        continue
                    if part_obj.metadata and "thought_signature" in part_obj.metadata:
                        part_dict["thoughtSignature"] = part_obj.metadata[
                            "thought_signature"
                        ]
                    current_parts.append(part_dict)

                if current_parts:
                    gemini_contents.append({"role": "model", "parts": current_parts})

            elif isinstance(msg, ToolMessage):
                from zhenxun.services.ai.core.messages import ToolReturnPart

                for part_obj in msg.content:
                    if isinstance(part_obj, ToolReturnPart):
                        result_obj = part_obj.output
                        if isinstance(result_obj, str):
                            try:
                                result_obj = json.loads(result_obj)
                            except json.JSONDecodeError:
                                pass
                        if not isinstance(result_obj, dict):
                            result_obj = {"result": result_obj}

                        func_resp = {
                            "name": part_obj.tool_name,
                            "response": result_obj,
                        }
                        if part_obj.tool_call_id and part_obj.tool_call_id != "unknown":
                            func_resp["id"] = part_obj.tool_call_id

                        current_parts.append({"functionResponse": func_resp})
                    else:
                        part_dict = await self.convert_part(part_obj)
                        if part_dict is not None:
                            current_parts.append(part_dict)

                if current_parts:
                    if gemini_contents and gemini_contents[-1]["role"] == "user":
                        gemini_contents[-1]["parts"].extend(current_parts)
                    else:
                        gemini_contents.append({"role": "user", "parts": current_parts})

        return gemini_contents

    def convert_messages(self, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        raise NotImplementedError("Use convert_messages_async for Gemini")


class GeminiToolSerializer(ToolSerializer):
    def sanitize_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        from zhenxun.services.ai.llm.engine.schema_transformer import (
            GeminiCyclicRefTransformer,
            GeminiDeepRefInlineTransformer,
            GeminiEnumTransformer,
            GeminiFormatTransformer,
            GeminiNullableUnionTransformer,
            RefComplianceTransformer,
            RemoveUnsupportedKeysTransformer,
            SchemaPipeline,
        )

        unsupported_keys = [
            "exclusiveMinimum",
            "exclusiveMaximum",
            "default",
            "title",
            "additionalProperties",
            "schema",
            "$schema",
            "id",
            "propertyNames",
            "patternProperties",
            "$defs",
            "definitions",
        ]
        pipeline = SchemaPipeline(
            [
                GeminiDeepRefInlineTransformer(),
                GeminiCyclicRefTransformer(schema),
                GeminiEnumTransformer(),
                GeminiNullableUnionTransformer(),
                GeminiFormatTransformer(),
                RemoveUnsupportedKeysTransformer(unsupported_keys),
                RefComplianceTransformer(),
            ]
        )
        return pipeline.run(schema)

    def format_tool_payload(
        self, tool_name: str, tool_description: str, sanitized_schema: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "name": tool_name,
            "description": tool_description,
            "parameters": sanitized_schema,
        }

    def serialize_server_tools(
        self, tools: list[Any], capabilities: ModelCapabilities
    ) -> list[dict[str, Any]]:
        """Gemini 接口的专门序列化，增加基于 capabilities 的鉴权"""
        res = []
        for t in tools:
            type_id = getattr(t, "type_id", "unknown")
            if type_id not in capabilities.supported_native_tools:
                continue
            if type_id == "web_search":
                res.append({"googleSearch": {}})
            elif type_id == "code_execution":
                res.append({"codeExecution": {}})
            elif type_id == "file_search":
                res.append({"fileSearch": {}})
            elif type_id == "google_map":
                res.append({"googleMaps": {}})
            elif type_id == "url_context":
                res.append({"urlContext": {}})
        return res


class GeminiResponseParser(ResponseParser):
    def validate_response(self, response_json: dict[str, Any]) -> None:
        if error := response_json.get("error"):
            code = error.get("code")
            message = error.get("message", "")
            status = error.get("status")
            details = error.get("details", [])

            if code == 429 or status == "RESOURCE_EXHAUSTED":
                is_quota = any(
                    d.get("reason") in ("QUOTA_EXCEEDED", "SERVICE_DISABLED")
                    for d in details
                    if isinstance(d, dict)
                )
                if is_quota or "quota" in message.lower():
                    raise QuotaExceededException(
                        f"Gemini配额耗尽: {message}",
                        details=error,
                    )
                raise RateLimitException(
                    f"Gemini速率限制: {message}",
                    details=error,
                )

            if code == 400 or status in ("INVALID_ARGUMENT", "FAILED_PRECONDITION"):
                raise InvalidRequestException(
                    f"Gemini参数错误: {message}",
                    details=error,
                )

        if prompt_feedback := response_json.get("promptFeedback"):
            if block_reason := prompt_feedback.get("blockReason"):
                raise ContentFilteredException(
                    f"内容被安全过滤: {block_reason}",
                    details={
                        "block_reason": block_reason,
                        "safety_ratings": prompt_feedback.get("safetyRatings"),
                    },
                )

    def parse(self, response_json: dict[str, Any]) -> ResponseData:
        self.validate_response(response_json)

        if "image_generation" in response_json and isinstance(
            response_json["image_generation"], dict
        ):
            candidates_source = response_json["image_generation"]
        else:
            candidates_source = response_json

        candidates = candidates_source.get("candidates", [])
        usage_info = response_json.get("usageMetadata")

        if not candidates:
            return ResponseData(raw_response=response_json)

        candidate = candidates[0]
        thought_signature: str | None = None

        content_data = candidate.get("content", {})
        parts = content_data.get("parts", [])

        content_parts: list[Any] = []
        thought_summary_parts: list[str] = []
        answer_parts = []

        for part in parts:
            part_signature = part.get("thoughtSignature")
            if part_signature and thought_signature is None:
                thought_signature = part_signature
            part_metadata: dict[str, Any] | None = None
            if part_signature:
                part_metadata = {"thought_signature": part_signature}

            if part.get("thought") is True:
                t_text = part.get("text", "")
                thought_summary_parts.append(t_text)
                content_parts.append(
                    ThoughtPart(thought_text=t_text, metadata=part_metadata)
                )

            elif "text" in part:
                answer_parts.append(part["text"])
                c_part = TextPart(text=part["text"], metadata=part_metadata)
                content_parts.append(c_part)

            elif "thoughtSummary" in part:
                thought_summary_parts.append(part["thoughtSummary"])
                content_parts.append(
                    ThoughtPart(
                        thought_text=part["thoughtSummary"], metadata=part_metadata
                    )
                )

            elif "inlineData" in part:
                inline_data = part["inlineData"]
                if "data" in inline_data:
                    decoded = base64.b64decode(inline_data["data"])
                    processed_img = process_image_data(decoded)
                    content_parts.append(
                        ImagePart(raw=processed_img)
                        if isinstance(processed_img, bytes)
                        else ImagePart(path=processed_img)
                    )

            elif "functionCall" in part or "toolCall" in part:
                fc_data = part.get("functionCall") or part.get("toolCall")
                fc_sig = part_signature
                try:
                    call_id = fc_data.get("id", "")
                    if not call_id:
                        call_id = f"call_{uuid.uuid4().hex[:16]}"
                    tc_part = ToolCallPart(
                        id=call_id,
                        tool_name=fc_data.get("name")
                        or fc_data.get("toolType", "unknown"),
                        args=fc_data.get("args", {}),
                    )
                    if fc_sig:
                        tc_part.metadata = {"thought_signature": fc_sig}
                    content_parts.append(tc_part)
                except Exception as e:
                    logger.warning(
                        f"解析Gemini functionCall时出错: {fc_data}, 错误: {e}"
                    )

            elif "functionResponse" in part or "toolResponse" in part:
                resp_data = part.get("functionResponse") or part.get("toolResponse")
                try:
                    call_id = resp_data.get("id", "")
                    if not call_id:
                        call_id = f"call_{uuid.uuid4().hex[:16]}"
                    tc_part = ToolReturnPart(
                        tool_call_id=call_id,
                        tool_name=resp_data.get("name")
                        or resp_data.get("toolType", ""),
                        output=resp_data.get("response", {}),
                    )
                    content_parts.append(tc_part)
                except Exception as e:
                    logger.warning(f"解析Gemini toolResponse时出错: {e}")

        content_parts.sort(key=lambda p: 1 if isinstance(p, ThoughtPart) else 0)

        grounding_metadata_obj = None
        if grounding_data := candidate.get("groundingMetadata"):
            try:
                sep_content = None
                sep_field = grounding_data.get("searchEntryPoint")
                if isinstance(sep_field, dict):
                    sep_content = sep_field.get("renderedContent")

                attributions = []
                if chunks := grounding_data.get("groundingChunks"):
                    for chunk in chunks:
                        if web := chunk.get("web"):
                            attributions.append(
                                LLMGroundingAttribution(
                                    title=web.get("title"),
                                    uri=web.get("uri"),
                                    snippet=web.get("snippet"),
                                    confidence_score=None,
                                )
                            )

                grounding_metadata_obj = LLMGroundingMetadata(
                    web_search_queries=grounding_data.get("webSearchQueries"),
                    grounding_attributions=attributions or None,
                    search_suggestions=grounding_data.get("searchSuggestions"),
                    search_entry_point=sep_content,
                    map_widget_token=grounding_data.get("googleMapsWidgetContextToken"),
                )
            except Exception as e:
                logger.warning(f"无法解析Grounding元数据: {grounding_data}, {e}")

        return ResponseData(
            content_parts=content_parts,
            usage_info=usage_info,
            raw_response=response_json,
            grounding_metadata=grounding_metadata_obj,
        )


class GeminiTextHandler(BaseTextHandler):
    """Gemini 文本对话处理器"""

    def __init__(self):
        self.converter = GeminiMessageConverter()
        self.serializer = GeminiToolSerializer()
        self.mapper = GeminiConfigMapper()
        self.parser = GeminiResponseParser()

    async def prepare_text_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: ChatRequest,
    ) -> RequestData:
        messages = request.messages
        config = request.config
        tools = request.tools
        effective_config = config if config is not None else identity.generation_config

        (
            tool_defs,
            client_executables,
            server_tools,
        ) = await self._resolve_and_split_tools(tools)

        from zhenxun.services.ai.config import get_llm_config

        gemini_settings = get_llm_config().provider_settings.gemini

        if server_tools and client_executables:
            has_mixed_tools_cap = identity.capabilities.has_feature("mixed_tools")
            if not gemini_settings.allow_mixed_tools or not has_mixed_tools_cap:
                server_tool_names = [
                    getattr(t, "name", "unknown") for t in server_tools
                ]
                reason = (
                    "全局开关 (allow_mixed_tools) 已关闭"
                    if not gemini_settings.allow_mixed_tools
                    else f"模型 {identity.model_name} 原生不支持工具混用"
                )
                logger.warning(
                    "🌐 [Gemini Adapter] 检测到请求中混用了"
                    "本地自定义工具与云端内置工具，"
                    f"但{reason}。"
                    f"自动拦截并屏蔽云端内置工具 {server_tool_names} 以防协议冲突。"
                )
                server_tools = []

        has_function_tools = len(client_executables) > 0

        is_structured = False
        if effective_config and effective_config.output:
            if (
                effective_config.output.response_schema
                or effective_config.output.response_format == ResponseFormat.JSON
                or effective_config.output.response_mime_type == "application/json"
            ):
                is_structured = True

        has_reasoning_cap = False
        if identity.capabilities and identity.capabilities.reasoning_mode in (
            ReasoningMode.BUDGET,
            ReasoningMode.LEVEL,
        ):
            has_reasoning_cap = True

        if (
            has_function_tools or is_structured or has_reasoning_cap
        ) and effective_config:
            if effective_config.common.reasoning_effort is None:
                if has_function_tools or is_structured:
                    reason_desc = "工具调用" if has_function_tools else "结构化输出"
                    logger.debug(
                        f"检测到{reason_desc}，自动为模型 "
                        f"{identity.model_name} 开启思维链增强"
                    )
                else:
                    logger.debug(
                        f"模型 {identity.model_name} 声明原生支持思维链，自动开启思维链"
                    )
                effective_config.common.reasoning_effort = "medium"
                effective_config.gemini_options.include_thoughts = True

        endpoint = getattr(adapter, "_get_gemini_endpoint")(identity, effective_config)
        url = adapter.get_api_url(identity, endpoint)
        headers = adapter.get_base_headers(api_key)

        system_instruction_parts: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                for part in msg.content:
                    part_dict = await self.converter.convert_part(part)
                    if part_dict is not None:
                        system_instruction_parts.append(part_dict)
                continue

        gemini_contents = await self.converter.convert_messages_async(messages)

        body: dict[str, Any] = {"contents": gemini_contents}

        if system_instruction_parts:
            body["systemInstruction"] = {"parts": system_instruction_parts}

        all_tools_for_request = []

        if server_tools:
            server_payloads = self.serializer.serialize_server_tools(
                server_tools, identity.capabilities
            )
            if server_payloads:
                all_tools_for_request.extend(server_payloads)
                if identity.capabilities.has_feature("server_side_tool_invocations"):
                    body.setdefault("toolConfig", {}).update(
                        {
                            "includeServerSideToolInvocations": True,
                        }
                    )

        has_user_functions = False
        if client_executables:
            function_declarations = self.serializer.serialize_tools(tool_defs)

            if function_declarations:
                all_tools_for_request.append(
                    {"functionDeclarations": function_declarations}
                )
                has_user_functions = True

        if all_tools_for_request:
            body["tools"] = all_tools_for_request

        tool_config_updates: dict[str, Any] = {}
        if effective_config and effective_config.gemini_options.retrieval_config:
            tool_config_updates["retrievalConfig"] = (
                effective_config.gemini_options.retrieval_config
            )

        if tool_config_updates:
            body.setdefault("toolConfig", {}).update(tool_config_updates)

        converted_params: dict[str, Any] = {}
        if effective_config:
            converted_params = self.mapper.map_config(
                effective_config, None, identity.capabilities
            )

        if converted_params:
            if "toolConfig" in converted_params:
                tool_config_payload = converted_params.pop("toolConfig")
                fc_config = tool_config_payload.get("functionCallingConfig")
                should_apply_fc = has_user_functions or (
                    fc_config and fc_config.get("mode") == "NONE"
                )
                if should_apply_fc:
                    body.setdefault("toolConfig", {}).update(tool_config_payload)
                elif fc_config and fc_config.get("mode") != "AUTO":
                    logger.debug(
                        "Gemini: 忽略针对纯内置工具的 functionCallingConfig (API限制)"
                    )

            if "safetySettings" in converted_params:
                body["safetySettings"] = converted_params.pop("safetySettings")

            if converted_params:
                body["generationConfig"] = converted_params

        return RequestData(url=url, headers=headers, body=body)

    def parse_text_response(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        response_json: dict[str, Any],
        is_advanced: bool = False,
    ) -> ResponseData:
        return self.parser.parse(response_json)


class GeminiEmbeddingHandler(BaseEmbeddingHandler):
    """Gemini 文本嵌入处理器"""

    async def prepare_embedding_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: EmbeddingRequest,
    ) -> RequestData:
        batch = request.batch
        config = request.config or LLMEmbeddingConfig()
        api_model_name = identity.model_name
        if not api_model_name.startswith("models/"):
            api_model_name = f"models/{api_model_name}"

        base_url = (
            identity.api_base.rstrip("/")
            if identity.api_base
            else "https://generativelanguage.googleapis.com"
        )
        url = f"{base_url}/v1beta/{api_model_name}:batchEmbedContents"
        headers = adapter.get_base_headers(api_key)

        converter = GeminiMessageConverter()

        requests_payload = []
        for payload in batch.payloads:
            gemini_parts = []
            text_prefix = ""

            if config.task_type == "RETRIEVAL_DOCUMENT":
                title_str = config.title if config.title else "none"
                text_prefix = f"title: {title_str} | text: "
            elif config.task_type:
                task_mapping = {
                    "RETRIEVAL_QUERY": "search result",
                    "QUESTION_ANSWERING": "question answering",
                    "FACT_VERIFICATION": "fact checking",
                    "CODE_RETRIEVAL_QUERY": "code retrieval",
                }
                mapped_task = task_mapping.get(str(config.task_type), "search result")
                text_prefix = f"task: {mapped_task} | query: "

            for i, part in enumerate(payload.parts):
                part_dict = await converter.convert_part(part)
                if part_dict:
                    if text_prefix and "text" in part_dict and i == 0:
                        part_dict["text"] = text_prefix + part_dict["text"]
                    gemini_parts.append(part_dict)

            if not gemini_parts:
                gemini_parts.append({"text": text_prefix + " "})

            request_item: dict[str, Any] = {
                "model": api_model_name,
                "content": {"parts": gemini_parts},
            }

            if config.output_dimensionality:
                request_item["output_dimensionality"] = config.output_dimensionality

            requests_payload.append(request_item)

        body = {"requests": requests_payload}
        return RequestData(url=url, headers=headers, body=body)

    def parse_embedding_response(
        self, adapter: BaseAdapter, response_json: dict[str, Any]
    ) -> list[list[float]]:
        adapter.validate_embedding_response(response_json)
        if "embeddings" not in response_json or not isinstance(
            response_json["embeddings"], list
        ):
            raise ResponseParseException(
                "Gemini嵌入响应缺少'embeddings'字段或格式不正确",
                details=response_json,
            )
        for item in response_json["embeddings"]:
            if "values" not in item:
                raise ResponseParseException(
                    "Gemini嵌入响应的条目中缺少'values'字段",
                    details=response_json,
                )

        try:
            embeddings_data = response_json["embeddings"]
            return [item["values"] for item in embeddings_data]
        except Exception as e:
            logger.error(
                f"解析Gemini嵌入响应时发生未知错误: {e}. 响应: {response_json}"
            )
            raise ResponseParseException(
                f"解析Gemini嵌入响应失败: {e}",
                cause=e,
            )


class GeminiImageHandler(BaseImageHandler):
    """Gemini 图像生成处理器"""

    def prepare_image_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: ImageRequest,
    ) -> RequestData:
        prompt = request.prompt
        images = request.images
        config = request.config
        endpoint = getattr(adapter, "_get_gemini_endpoint")(identity, config)
        url = adapter.get_api_url(identity, endpoint)
        headers = adapter.get_base_headers(api_key)

        parts: list[dict[str, Any]] = [{"text": prompt}]

        if images:
            for img in images:
                if isinstance(img, bytes):
                    img_bytes = img
                elif hasattr(img, "read_bytes"):
                    img_bytes = img.read_bytes()
                elif isinstance(img, str) and img.startswith("data:image"):
                    b64_data = img.split(",", 1)[1]
                    img_bytes = base64.b64decode(b64_data)
                else:
                    raise InvalidRequestException(
                        "Gemini 图像编辑仅支持 bytes/Path/base64 URI",
                    )
                mime_type = "image/jpeg"
                if img_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                    mime_type = "image/png"
                elif img_bytes.startswith(b"GIF87a") or img_bytes.startswith(b"GIF89a"):
                    mime_type = "image/gif"
                elif img_bytes.startswith(b"RIFF") and img_bytes[8:12] == b"WEBP":
                    mime_type = "image/webp"

                b64_str = base64.b64encode(img_bytes).decode("utf-8")
                parts.append(
                    {
                        "inline_data": {"mime_type": mime_type, "data": b64_str},
                    }
                )

        body: dict[str, Any] = {"contents": [{"parts": parts}]}

        if config is None:
            config = GenerationConfig()

        mapper = GeminiConfigMapper()
        gen_config = mapper.map_config(config, None, identity.capabilities)

        if gen_config:
            body["generationConfig"] = gen_config

        return RequestData(url=url, headers=headers, body=body)

    def parse_image_response(
        self, adapter: BaseAdapter, response_json: dict[str, Any]
    ) -> ResponseData:
        parser = GeminiResponseParser()
        return parser.parse(response_json)


class GeminiAudioHandler(BaseAudioHandler):
    """Gemini 文本转语音处理器"""

    def prepare_speech_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: SpeechRequest,
    ) -> RequestData:
        input_text = request.input_text
        config = request.config or TTSConfig()

        config_voice = config.gemini_options.voice_id
        voice = (
            config_voice
            or request.voice
            or identity.capabilities.default_voice_id
            or "Aoede"
        )

        endpoint = getattr(adapter, "_get_gemini_endpoint")(identity, None)
        url = adapter.get_api_url(identity, endpoint)
        headers = adapter.get_base_headers(api_key)

        speech_config: dict[str, Any] = {}
        if config.gemini_options.multi_speaker and config.gemini_options.second_voice:
            speech_config["multiSpeakerVoiceConfig"] = {
                "speakerVoiceConfigs": [
                    {
                        "speaker": "Speaker1",
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}},
                    },
                    {
                        "speaker": "Speaker2",
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": config.gemini_options.second_voice
                            }
                        },
                    },
                ]
            }
        else:
            speech_config["voiceConfig"] = {"prebuiltVoiceConfig": {"voiceName": voice}}

        body = {
            "contents": [{"parts": [{"text": input_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": speech_config,
            },
        }
        return RequestData(url=url, headers=headers, body=body)

    async def parse_speech_response(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        raw_response: httpx.Response,
    ) -> AudioResponse:
        resp_bytes = await raw_response.aread()
        data = json.loads(resp_bytes)
        adapter.validate_response(data)

        b64_data = ""
        try:
            b64_data = data["candidates"][0]["content"]["parts"][0]["inlineData"][
                "data"
            ]
        except (KeyError, IndexError):
            raise ResponseParseException("Gemini 响应中未找到音频数据", details=data)

        audio_bytes = base64.b64decode(b64_data)

        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            wav_file.writeframes(audio_bytes)

        from zhenxun.services.ai.core.messages import UsageInfo

        return AudioResponse(
            audio_bytes=wav_io.getvalue(),
            audio_format="wav",
            usage=UsageInfo(),
            model_name=identity.model_name,
        )
