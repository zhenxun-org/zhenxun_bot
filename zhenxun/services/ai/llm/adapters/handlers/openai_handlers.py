import base64
import binascii
import json
from pathlib import Path
from typing import Any

import httpx
import json_repair

from zhenxun.services.ai.core.exceptions import (
    AuthenticationException,
    ConfigurationException,
    ContentFilteredException,
    ContextLengthExceededException,
    InvalidRequestException,
    QuotaExceededException,
    RateLimitException,
    ResponseParseException,
)
from zhenxun.services.ai.core.messages import (
    AssistantMessage,
    AudioResponse,
    ChatRequest,
    EmbeddingRequest,
    ImagePart,
    ImageRequest,
    LLMMessage,
    RerankDocument,
    RerankRequest,
    RerankResult,
    SpeechRequest,
    SystemMessage,
    TextPart,
    ThoughtPart,
    ToolCallPart,
    ToolMessage,
    UserMessage,
)
from zhenxun.services.ai.core.models import (
    ModelCapabilities,
    ModelDetail,
    ModelIdentity,
)
from zhenxun.services.ai.core.options import (
    GenerationConfig,
    LLMEmbeddingConfig,
    ResponseFormat,
    StructuredOutputStrategy,
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
    BaseRerankHandler,
    BaseTextHandler,
    ConfigMapper,
    MessageConverter,
    ResponseParser,
    ToolSerializer,
)


class OpenAIConfigMapper(ConfigMapper):
    def __init__(self, api_type: str = "openai"):
        self.api_type = api_type

    def map_config(
        self,
        config: GenerationConfig,
        model_detail: ModelDetail | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        strategy = config.output.structured_output_strategy
        if strategy is None:
            strategy = (
                StructuredOutputStrategy.TOOL_CALL
                if self.api_type == "deepseek"
                else StructuredOutputStrategy.NATIVE
            )

        if config.common:
            if config.common.temperature is not None:
                params["temperature"] = config.common.temperature
            if config.common.max_tokens is not None:
                params["max_tokens"] = config.common.max_tokens
            if config.common.top_k is not None:
                params["top_k"] = config.common.top_k
            if config.common.top_p is not None:
                params["top_p"] = config.common.top_p
            if config.common.frequency_penalty is not None:
                params["frequency_penalty"] = config.common.frequency_penalty
            if config.common.presence_penalty is not None:
                params["presence_penalty"] = config.common.presence_penalty
            if config.common.stop is not None:
                params["stop"] = config.common.stop

            if config.common.repetition_penalty is not None:
                if self.api_type == "openai":
                    pass
                else:
                    params["repetition_penalty"] = config.common.repetition_penalty

        if config.common.reasoning_effort:
            effort = str(config.common.reasoning_effort).lower()

            if capabilities and capabilities.reasoning_effort_map:
                effort = capabilities.reasoning_effort_map.get(effort, effort)

            if (
                effort == "none"
                and capabilities
                and capabilities.supports_thinking_toggle
            ):
                params["thinking"] = {"type": "disabled"}
            else:
                params["reasoning_effort"] = effort

        if isinstance(config.output.response_format, dict):
            params["response_format"] = config.output.response_format
        elif (
            config.output.response_format == ResponseFormat.JSON
            and strategy == StructuredOutputStrategy.NATIVE
        ):
            if config.output.response_schema:
                serializer = OpenAIToolSerializer(api_type=self.api_type)
                sanitized = serializer.sanitize_schema(config.output.response_schema)
                params["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "structured_response",
                        "schema": sanitized,
                        "strict": True,
                    },
                }
            else:
                params["response_format"] = {"type": "json_object"}

        if config.custom_kwargs:
            mapped_custom = config.custom_kwargs.copy()
            mapped_custom.pop("__cache_ttl__", None)
            if "repetition_penalty" in mapped_custom and self.api_type == "openai":
                mapped_custom.pop("repetition_penalty")

            params.update(mapped_custom)

        return params


class OpenAIMessageConverter(MessageConverter):
    def __init__(self, api_type: str = "openai"):
        self.api_type = api_type

    async def convert_messages_async(
        self, messages: list[LLMMessage]
    ) -> list[dict[str, Any]]:
        openai_messages: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                openai_msg: dict[str, Any] = {"role": "system"}
            elif isinstance(msg, UserMessage):
                openai_msg: dict[str, Any] = {"role": "user"}
            elif isinstance(msg, AssistantMessage):
                openai_msg: dict[str, Any] = {"role": "assistant"}
            elif isinstance(msg, ToolMessage):
                openai_msg: dict[str, Any] = {"role": "tool"}
            else:
                openai_msg: dict[str, Any] = {"role": msg.role}

            if isinstance(msg, ToolMessage):
                returns = msg.tool_returns
                if returns:
                    openai_msg["tool_call_id"] = returns[0].tool_call_id
                    openai_msg["name"] = returns[0].tool_name
                    out_val = returns[0].output
                    openai_msg["content"] = (
                        out_val
                        if isinstance(out_val, str)
                        else json.dumps(out_val, ensure_ascii=False)
                    )
            else:
                if len(msg.content) == 1 and isinstance(msg.content[0], TextPart):
                    openai_msg["content"] = msg.content[0].text
                else:
                    content_parts = []
                    for part in msg.content:
                        if isinstance(part, TextPart):
                            content_parts.append({"type": "text", "text": part.text})
                        elif isinstance(part, ImagePart):
                            if part.url is not None:
                                content_parts.append(
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": part.url},
                                    }
                                )
                            else:
                                data_uri = await part.get_data_uri("image/png")
                                content_parts.append(
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": data_uri},
                                    }
                                )
                    openai_msg["content"] = content_parts

            if isinstance(msg, AssistantMessage):
                thought_text = "\n".join(
                    p.thought_text
                    for p in msg.content
                    if isinstance(p, ThoughtPart) and p.thought_text
                ).strip()

                if thought_text:
                    openai_msg["reasoning_content"] = thought_text
                else:
                    openai_msg["reasoning_content"] = ""

            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                assistant_tool_calls = []
                for call in msg.tool_calls:
                    assistant_tool_calls.append(
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.tool_name,
                                "arguments": call.args
                                if isinstance(call.args, str)
                                else json.dumps(call.args, ensure_ascii=False),
                            },
                        }
                    )
                openai_msg["tool_calls"] = assistant_tool_calls

            if not openai_msg.get("content") and not isinstance(
                openai_msg.get("content"), str
            ):
                openai_msg["content"] = ""

            if (
                openai_msg.get("content") == ""
                and not openai_msg.get("tool_calls")
                and "tool_call_id" not in openai_msg
            ):
                continue

            openai_messages.append(openai_msg)
        return openai_messages


class OpenAIToolSerializer(ToolSerializer):
    def __init__(self, api_type: str = "openai"):
        self.api_type = api_type

    def sanitize_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        from zhenxun.services.ai.llm.engine.schema_transformer import (
            OpenAIUnionFlattenTransformer,
            RefComplianceTransformer,
            RemoveUnsupportedKeysTransformer,
            RootRefInlineTransformer,
            SchemaPipeline,
            StrictObjectTransformer,
            TypeEnforcerTransformer,
        )

        unsupported_keys = [
            "default",
            "minLength",
            "maxLength",
            "pattern",
            "format",
            "minimum",
            "maximum",
            "multipleOf",
            "patternProperties",
            "propertyNames",
            "minItems",
            "maxItems",
            "uniqueItems",
            "$schema",
            "title",
        ]
        pipeline = SchemaPipeline(
            [
                RootRefInlineTransformer(),
                OpenAIUnionFlattenTransformer(),
                TypeEnforcerTransformer(),
                RemoveUnsupportedKeysTransformer(unsupported_keys),
                StrictObjectTransformer(),
                RefComplianceTransformer(),
            ]
        )
        return pipeline.run(schema)

    def format_tool_payload(
        self, tool_name: str, tool_description: str, sanitized_schema: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_description,
                "parameters": sanitized_schema,
                "strict": True,
            },
        }

    def serialize_server_tools(
        self, tools: list[Any], capabilities: ModelCapabilities
    ) -> list[dict[str, Any]]:
        """标准 OpenAI 协议 (/v1/chat/completions) 不支持原生云端工具传递"""
        return []


class OpenAIResponseParser(ResponseParser):
    def validate_response(self, response_json: dict[str, Any]) -> None:
        if response_json.get("error"):
            error_info = response_json["error"]
            error_message = str(error_info)
            if isinstance(error_info, dict):
                error_message = error_info.get("message", error_message)
                error_code = error_info.get("code", "unknown")

                if (
                    error_code in ("invalid_api_key", "authentication_failed")
                    or "permission" in error_message.lower()
                ):
                    raise AuthenticationException(
                        f"鉴权失败: {error_message}", details={"api_error": error_info}
                    )
                elif error_code in ("insufficient_quota", "quota_exceeded"):
                    raise QuotaExceededException(
                        f"配额耗尽: {error_message}", details={"api_error": error_info}
                    )
                elif error_code == "rate_limit_exceeded":
                    raise RateLimitException(
                        f"请求限流: {error_message}", details={"api_error": error_info}
                    )
                elif error_code in ("model_not_found", "invalid_model"):
                    raise ConfigurationException(
                        f"模型配置错误: {error_message}",
                        details={"api_error": error_info},
                    )
                elif error_code in ("context_length_exceeded", "max_tokens_exceeded"):
                    raise ContextLengthExceededException(
                        f"上下文超限: {error_message}",
                        details={"api_error": error_info},
                    )
                elif error_code in ("invalid_request_error", "invalid_parameter"):
                    raise InvalidRequestException(
                        f"请求参数错误: {error_message}",
                        details={"api_error": error_info},
                    )

            raise ResponseParseException(
                f"API请求失败: {error_message}",
                details={"api_error": error_info},
            )

    def parse(self, response_json: dict[str, Any]) -> ResponseData:
        self.validate_response(response_json)

        choices = response_json.get("choices", [])
        if not choices:
            return ResponseData(raw_response=response_json)

        choice = choices[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        reasoning_content = message.get("reasoning_content", None)
        reasoning_details = message.get("reasoning_details", None)
        refusal = message.get("refusal")

        if refusal:
            raise ContentFilteredException(
                f"模型拒绝生成请求: {refusal}",
                details={"refusal": refusal},
            )

        if content:
            content = content.strip()

        images_payload: list[bytes | Path] = []
        if content and content.startswith("{") and content.endswith("}"):
            try:
                content_json = json.loads(content)
                if "b64_json" in content_json:
                    b64_str = content_json["b64_json"]
                    if isinstance(b64_str, str) and b64_str.startswith("data:"):
                        b64_str = b64_str.split(",", 1)[1]
                    decoded = base64.b64decode(b64_str)
                    images_payload.append(process_image_data(decoded))
                    content = "[图片已生成]"
                elif "data" in content_json and isinstance(content_json["data"], str):
                    b64_str = content_json["data"]
                    if b64_str.startswith("data:"):
                        b64_str = b64_str.split(",", 1)[1]
                    decoded = base64.b64decode(b64_str)
                    images_payload.append(process_image_data(decoded))
                    content = "[图片已生成]"

            except (json.JSONDecodeError, KeyError, binascii.Error):
                pass
        elif (
            "images" in message
            and isinstance(message["images"], list)
            and message["images"]
        ):
            for image_info in message["images"]:
                if image_info.get("type") == "image_url":
                    image_url_obj = image_info.get("image_url", {})
                    url_str = image_url_obj.get("url", "")
                    if url_str.startswith("data:image"):
                        try:
                            b64_data = url_str.split(",", 1)[1]
                            decoded = base64.b64decode(b64_data)
                            images_payload.append(process_image_data(decoded))
                        except (IndexError, binascii.Error) as e:
                            logger.warning(f"解析OpenRouter Base64图片数据失败: {e}")

            if images_payload:
                content = content if content else "[图片已生成]"

        content_parts = []
        if content:
            content_parts.append(TextPart(text=content))
        if reasoning_details:
            combined_thought_text = ""
            for detail in reasoning_details:
                if isinstance(detail, dict) and "text" in detail:
                    combined_thought_text += detail["text"]
            if combined_thought_text:
                content_parts.append(
                    ThoughtPart(
                        thought_text=combined_thought_text,
                        metadata={"raw_reasoning_details": reasoning_details},
                    )
                )
        elif reasoning_content:
            content_parts.append(ThoughtPart(thought_text=reasoning_content))
        for img in images_payload:
            content_parts.append(
                ImagePart(raw=img) if isinstance(img, bytes) else ImagePart(path=img)
            )

        if message_tool_calls := message.get("tool_calls"):
            for tc_data in message_tool_calls:
                try:
                    if tc_data.get("type") == "function":
                        raw_arguments = tc_data["function"]["arguments"]

                        content_parts.append(
                            ToolCallPart(
                                id=tc_data["id"],
                                tool_name=tc_data["function"]["name"],
                                args=raw_arguments,
                            )
                        )
                except KeyError as e:
                    logger.warning(
                        f"解析OpenAI工具调用数据时缺少键: {tc_data}, 错误: {e}"
                    )
                except Exception as e:
                    logger.warning(
                        f"解析OpenAI工具调用数据时出错: {tc_data}, 错误: {e}"
                    )

        usage_info = response_json.get("usage")

        return ResponseData(
            content_parts=content_parts,
            usage_info=usage_info,
            raw_response=response_json,
        )


class ResponsesConfigMapper(OpenAIConfigMapper):
    """针对 OpenAI Responses API 的配置映射器"""

    def map_config(
        self,
        config: GenerationConfig,
        model_detail: ModelDetail | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> dict[str, Any]:
        params = super().map_config(config, model_detail, capabilities)

        if "reasoning_effort" in params:
            effort_val = params.pop("reasoning_effort")
            params["reasoning"] = {"effort": effort_val, "summary": "auto"}
        else:
            params["reasoning"] = {"summary": "auto"}

        if "response_format" in params:
            fmt = params.pop("response_format")
            if isinstance(fmt, dict) and fmt.get("type") == "json_schema":
                json_schema_dict = fmt.get("json_schema", {})
                params["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": json_schema_dict.get("name", "structured_response"),
                        "strict": json_schema_dict.get("strict", True),
                        "schema": json_schema_dict.get("schema", {}),
                    }
                }
            elif isinstance(fmt, dict):
                params["text"] = {"format": fmt}

        return params


class ResponsesMessageConverter(MessageConverter):
    """针对 OpenAI Responses API 的消息转换器"""

    async def convert_messages_async(
        self, messages: list[LLMMessage]
    ) -> list[dict[str, Any]]:
        input_items: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.role

            if isinstance(msg, ToolMessage):
                returns = msg.tool_returns
                if returns:
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": returns[0].tool_call_id,
                            "output": returns[0].output
                            if isinstance(returns[0].output, str)
                            else json.dumps(returns[0].output, ensure_ascii=False),
                        }
                    )
                    continue

            content_list: list[dict[str, Any]] = []
            for part in msg.content:
                if part is None:
                    continue

                if isinstance(part, TextPart):
                    c_type = "output_text" if role == "assistant" else "input_text"
                    content_list.append({"type": c_type, "text": part.text})
                elif isinstance(part, ImagePart):
                    if part.url is not None:
                        content_list.append(
                            {"type": "input_image", "image_url": part.url}
                        )
                    else:
                        data_uri = await part.get_data_uri("image/png")
                        content_list.append(
                            {"type": "input_image", "image_url": data_uri}
                        )
                elif isinstance(part, dict):
                    part_type = part.get("type")
                    if part_type == "text":
                        c_type = "output_text" if role == "assistant" else "input_text"
                        content_list.append(
                            {"type": c_type, "text": part.get("text", "")}
                        )
                    elif part_type in {"image", "image_url"}:
                        image_src = part.get("image_url") or part.get("url", "")
                        content_list.append(
                            {"type": "input_image", "image_url": image_src}
                        )

            if content_list:
                input_items.append({"role": role, "content": content_list})

            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": tc.id,
                            "name": tc.tool_name,
                            "arguments": tc.args
                            if isinstance(tc.args, str)
                            else json.dumps(tc.args, ensure_ascii=False),
                        }
                    )

        return input_items


class ResponsesToolSerializer(OpenAIToolSerializer):
    """针对 OpenAI Responses API 的工具序列化器"""

    def format_tool_payload(
        self, tool_name: str, tool_description: str, sanitized_schema: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "name": tool_name,
            "description": tool_description,
            "parameters": sanitized_schema,
            "strict": True,
        }

    def serialize_server_tools(
        self, tools: list[Any], capabilities: ModelCapabilities
    ) -> list[dict[str, Any]]:
        """OpenAI Responses API 的专门序列化，增加基于 capabilities 的鉴权"""
        res = []
        for t in tools:
            type_id = getattr(t, "type_id", "unknown")
            if type_id not in capabilities.supported_native_tools:
                continue
            if type_id == "web_search":
                payload = {"type": "web_search"}
                if getattr(t, "domain_filters", None):
                    payload["filters"] = t.domain_filters
                res.append(payload)
            elif type_id == "code_execution":
                res.append({"type": "code_interpreter"})
            elif type_id == "computer_use":
                res.append(
                    {
                        "type": "computer_use",
                        "display_width_px": getattr(t, "display_width_px", 1024),
                        "display_height_px": getattr(t, "display_height_px", 768),
                    }
                )
            elif type_id == "file_search":
                res.append({"type": "file_search"})
        return res


class ResponsesResponseParser(OpenAIResponseParser):
    """针对 OpenAI Responses API 的响应解析器"""

    def parse(self, response_json: dict[str, Any]) -> ResponseData:
        content_parts: list[Any] = []
        text_content = ""
        thought_content = ""

        for item in response_json.get("output", []):
            if item.get("type") == "message" and item.get("role") == "assistant":
                for content_item in item.get("content", []):
                    if content_item.get("type") == "output_text":
                        text_content += content_item.get("text", "")
                    elif content_item.get("type") == "refusal":
                        raise ContentFilteredException(
                            f"模型拒绝生成: {content_item.get('refusal')}",
                        )
            elif item.get("type") == "function_call":
                content_parts.append(
                    ToolCallPart(
                        id=item.get("call_id", ""),
                        tool_name=item.get("name", ""),
                        args=item.get("arguments", "{}"),
                    )
                )
            elif item.get("type") == "reasoning":
                for summary_item in item.get("summary", []):
                    if summary_item.get("type") == "summary_text":
                        thought_content += summary_item.get("text", "")

        if text_content:
            content_parts.insert(0, TextPart(text=text_content))
        if thought_content:
            content_parts.insert(0, ThoughtPart(thought_text=thought_content))

        return ResponseData(
            content_parts=content_parts,
            usage_info=response_json.get("usage"),
            raw_response=response_json,
        )


class OpenAITextHandler(BaseTextHandler):
    """标准 OpenAI 协议的文本对话处理器"""

    def __init__(self, api_type: str = "openai"):
        self.api_type = api_type
        self.converter = OpenAIMessageConverter(api_type=api_type)
        self.serializer = OpenAIToolSerializer(api_type=api_type)
        self.mapper = OpenAIConfigMapper(api_type=api_type)
        self.parser = OpenAIResponseParser()

    def _build_base_body(
        self, identity: ModelIdentity, messages: list[Any]
    ) -> dict[str, Any]:
        return {
            "model": identity.model_name,
            "messages": messages,
        }

    async def prepare_text_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: ChatRequest,
    ) -> RequestData:
        endpoint = getattr(adapter, "get_chat_endpoint")(identity)
        url = adapter.get_api_url(identity, endpoint)
        headers = adapter.get_base_headers(api_key)

        messages = request.messages
        tools = request.tools
        tool_choice = request.tool_choice
        config = request.config
        effective_config = config if config is not None else identity.generation_config
        structured_strategy = (
            effective_config.output.structured_output_strategy
            if effective_config and effective_config.output
            else None
        )
        if structured_strategy is None:
            structured_strategy = (
                StructuredOutputStrategy.TOOL_CALL
                if identity.api_type == "deepseek"
                and identity.model_name == "deepseek-chat"
                else StructuredOutputStrategy.NATIVE
            )

        tool_defs, _, server_tools = await self._resolve_and_split_tools(tools)

        openai_tools: list[dict[str, Any]] | None = None
        if tool_defs:
            openai_tools = self.serializer.serialize_tools(tool_defs)

        final_tool_choice = tool_choice
        if final_tool_choice is None and effective_config:
            mode = effective_config.tools.mode
            if mode == "ANY":
                allowed = effective_config.tools.allowed_function_names
                if allowed:
                    if len(allowed) == 1:
                        if isinstance(self, OpenAIResponsesTextHandler):
                            final_tool_choice = {"type": "function", "name": allowed[0]}
                        else:
                            final_tool_choice = {
                                "type": "function",
                                "function": {"name": allowed[0]},
                            }
                    else:
                        logger.warning(
                            "OpenAI API 不支持多个 allowed_function_names，"
                            "降级为 required。"
                        )
                        final_tool_choice = "required"
                else:
                    final_tool_choice = "required"
            elif mode == "NONE":
                final_tool_choice = "none"
            elif mode == "AUTO":
                final_tool_choice = "auto"

        if (
            structured_strategy == StructuredOutputStrategy.TOOL_CALL
            and effective_config
            and effective_config.output
            and effective_config.output.response_schema
        ):
            sanitized_schema = self.serializer.sanitize_schema(
                effective_config.output.response_schema
            )
            structured_tool = {
                "type": "function",
                "function": {
                    "name": "return_structured_response",
                    "description": "Output the final structured response.",
                    "parameters": sanitized_schema,
                },
            }
            structured_tool["function"]["strict"] = True

            if isinstance(self, OpenAIResponsesTextHandler):
                func_data = structured_tool.pop("function")
                structured_tool.update(func_data)
                final_tool_choice = {
                    "type": "function",
                    "name": "return_structured_response",
                }
            else:
                final_tool_choice = {
                    "type": "function",
                    "function": {"name": "return_structured_response"},
                }

            if openai_tools is None:
                openai_tools = []
            openai_tools.append(structured_tool)

        converted_messages = await self.converter.convert_messages_async(messages)
        body = self._build_base_body(identity, converted_messages)

        if openai_tools:
            body["tools"] = openai_tools
        if final_tool_choice is not None and openai_tools:
            body["tool_choice"] = final_tool_choice

        config_params = {}
        if effective_config:
            config_params = self.mapper.map_config(
                effective_config, None, identity.capabilities
            )
        body.update(config_params)

        if server_tools:
            if openai_tools is None:
                openai_tools = []
            server_payloads = self.serializer.serialize_server_tools(
                server_tools, identity.capabilities
            )
            if server_payloads:
                openai_tools.extend(server_payloads)
            if openai_tools:
                body["tools"] = openai_tools

        if "tools" not in body and "tool_choice" in body:
            body.pop("tool_choice")

        return RequestData(url=url, headers=headers, body=body)

    def parse_text_response(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        response_json: dict[str, Any],
        is_advanced: bool = False,
    ) -> ResponseData:
        response_data = self.parser.parse(response_json)

        tool_calls = [
            p for p in response_data.content_parts if isinstance(p, ToolCallPart)
        ]
        if tool_calls:
            target_tool = next(
                (
                    tc
                    for tc in tool_calls
                    if tc.tool_name == "return_structured_response"
                ),
                None,
            )
            if target_tool:
                args_data = target_tool.args
                if isinstance(args_data, str):
                    response_data.text = json_repair.repair_json(args_data)
                else:
                    response_data.text = json.dumps(args_data, ensure_ascii=False)
                response_data.content_parts = [
                    p
                    for p in response_data.content_parts
                    if not (
                        isinstance(p, ToolCallPart)
                        and p.tool_name == "return_structured_response"
                    )
                ]

        return response_data


class OpenAIResponsesTextHandler(OpenAITextHandler):
    """OpenAI v1/responses 协议的文本对话处理器"""

    def __init__(self, api_type: str = "openai_responses"):
        super().__init__(api_type=api_type)
        self.converter = ResponsesMessageConverter()
        self.serializer = ResponsesToolSerializer(api_type=api_type)
        self.mapper = ResponsesConfigMapper(api_type=api_type)
        self.parser = ResponsesResponseParser()

    def _build_base_body(
        self, identity: ModelIdentity, messages: list[Any]
    ) -> dict[str, Any]:
        return {
            "model": identity.model_name,
            "input": messages,
        }


class OpenAIEmbeddingHandler(BaseEmbeddingHandler):
    """OpenAI 嵌入向量处理器"""

    async def prepare_embedding_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: EmbeddingRequest,
    ) -> RequestData:
        batch = request.batch
        config = request.config or LLMEmbeddingConfig()
        texts = batch.to_text_only(f"{identity.model_name} (API: {adapter.api_type})")

        endpoint = getattr(adapter, "get_embedding_endpoint")(identity)
        url = adapter.get_api_url(identity, endpoint)
        headers = adapter.get_base_headers(api_key)

        body = {
            "model": identity.model_name,
            "input": texts,
        }

        if config.output_dimensionality:
            body["dimensions"] = config.output_dimensionality
        if config.task_type:
            body["task"] = config.task_type
        if config.encoding_format and config.encoding_format != "float":
            body["encoding_format"] = config.encoding_format

        return RequestData(url=url, headers=headers, body=body)

    def parse_embedding_response(
        self, adapter: BaseAdapter, response_json: dict[str, Any]
    ) -> list[list[float]]:
        adapter.validate_response(response_json)
        try:
            data = response_json.get("data", [])
            if not data:
                raise ResponseParseException(
                    "嵌入响应中没有数据",
                    details=response_json,
                )
            embeddings = []
            for item in data:
                if "embedding" in item:
                    embeddings.append(item["embedding"])
                else:
                    raise ResponseParseException(
                        "嵌入响应格式错误：缺少embedding字段",
                        details=item,
                    )
            return embeddings
        except Exception as e:
            logger.error(f"解析嵌入响应失败: {e}", e=e)
            raise ResponseParseException(
                f"解析嵌入响应失败: {e}",
                cause=e,
            )


class OpenAIImageHandler(BaseImageHandler):
    """OpenAI 图像生成处理器"""

    def prepare_image_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: ImageRequest,
    ) -> RequestData:
        headers = adapter.get_base_headers(api_key)
        prompt = request.prompt
        images = request.images
        config = request.config

        body: dict[str, Any] = {
            "model": identity.model_name,
            "prompt": prompt,
            "response_format": "b64_json",
        }

        if config:
            if config.media.resolution:
                res_str = str(config.media.resolution).upper()
                aspect_ratio = (
                    str(config.media.aspect_ratio).upper()
                    if config.media.aspect_ratio
                    else ""
                )

                if res_str == "1K":
                    if "16:9" in aspect_ratio or "3:2" in aspect_ratio:
                        res_str = "1536x1024"
                    elif "9:16" in aspect_ratio or "2:3" in aspect_ratio:
                        res_str = "1024x1536"
                    else:
                        res_str = "1024x1024"
                elif res_str == "2K":
                    if "16:9" in aspect_ratio or "3:2" in aspect_ratio:
                        res_str = "2048x1152"
                    elif "9:16" in aspect_ratio or "2:3" in aspect_ratio:
                        res_str = "1152x2048"
                    else:
                        res_str = "2048x2048"
                elif res_str == "4K":
                    if "9:16" in aspect_ratio or "2:3" in aspect_ratio:
                        res_str = "2160x3840"
                    elif "1:1" in aspect_ratio:
                        res_str = "2880x2880"
                    else:
                        res_str = "3840x2160"

                body["size"] = res_str

            if config.media.quality:
                body["quality"] = config.media.quality

        if not images:
            endpoint = "/v1/images/generations"
            url = adapter.get_api_url(identity, endpoint)
            return RequestData(url=url, headers=headers, body=body)
        else:
            endpoint = "/v1/images/edits"
            url = adapter.get_api_url(identity, endpoint)
            files = []
            file_key = "image[]" if len(images) > 1 else "image"

            for i, img_source in enumerate(images):
                img_bytes = None
                if isinstance(img_source, bytes):
                    img_bytes = img_source
                elif hasattr(img_source, "read_bytes"):
                    img_bytes = img_source.read_bytes()
                elif isinstance(img_source, str) and img_source.startswith(
                    "data:image"
                ):
                    b64_data = img_source.split(",", 1)[1]
                    img_bytes = base64.b64decode(b64_data)
                else:
                    raise InvalidRequestException(
                        "OpenAI 图像编辑仅支持 bytes/Path/base64 URI",
                    )

                files.append((file_key, (f"image_{i}.png", img_bytes, "image/png")))

            headers.pop("Content-Type", None)
            return RequestData(url=url, headers=headers, body=body, files=files)

    def parse_image_response(
        self, adapter: BaseAdapter, response_json: dict[str, Any]
    ) -> ResponseData:
        adapter.validate_response(response_json)

        images_data: list[bytes | Path | str] = []
        data_list = response_json.get("data", [])

        for item in data_list:
            if "b64_json" in item:
                try:
                    b64_str = item["b64_json"]
                    if b64_str.startswith("data:"):
                        b64_str = b64_str.split(",", 1)[1]
                    img = base64.b64decode(b64_str)
                    images_data.append(process_image_data(img))
                except Exception as exc:
                    logger.error(f"Base64 解码失败: {exc}")
            elif "url" in item:
                images_data.append(item["url"])

        content_parts = []
        for img in images_data:
            if isinstance(img, str) and img.startswith("http"):
                content_parts.append(ImagePart(url=img))
            elif isinstance(img, bytes):
                content_parts.append(ImagePart(raw=img))
            elif isinstance(img, str):
                content_parts.append(ImagePart(path=Path(img)))
            else:
                content_parts.append(ImagePart(path=img))

        if not content_parts:
            raise ResponseParseException("OpenAI 图像生成响应中未找到有效的图片数据")

        return ResponseData(
            content_parts=content_parts,
            raw_response=response_json,
        )


class OpenAIRerankHandler(BaseRerankHandler):
    """OpenAI (扩展) 重排处理器"""

    def prepare_rerank_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: RerankRequest,
    ) -> RequestData:
        endpoint = "/v1/rerank"
        url = adapter.get_api_url(identity, endpoint)
        headers = adapter.get_base_headers(api_key)

        safe_documents = []
        for doc in request.documents:
            if isinstance(doc, dict):
                safe_documents.append(doc.get("text", str(doc)))
            else:
                safe_documents.append(str(doc))

        body = {
            "model": identity.model_name,
            "query": request.query,
            "documents": safe_documents,
            "top_n": request.top_n,
        }
        return RequestData(url=url, headers=headers, body=body)

    def parse_rerank_response(
        self, adapter: BaseAdapter, response_json: dict[str, Any]
    ) -> list[RerankResult]:
        adapter.validate_response(response_json)
        results = []
        for item in response_json.get("results", []):
            doc = item.get("document", {})
            r_doc = (
                RerankDocument(text=doc.get("text"), image=doc.get("image"))
                if isinstance(doc, dict)
                else RerankDocument(text=str(doc))
            )
            results.append(
                RerankResult(
                    index=item["index"],
                    relevance_score=item["relevance_score"],
                    document=r_doc,
                )
            )
        return results


class OpenAIAudioHandler(BaseAudioHandler):
    """OpenAI 文本转语音处理器"""

    def prepare_speech_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: SpeechRequest,
    ) -> RequestData:
        endpoint = "/v1/audio/speech"
        url = adapter.get_api_url(identity, endpoint)
        headers = adapter.get_base_headers(api_key)

        config_voice = (
            request.config.openai_options.voice_id if request.config else None
        )
        voice = (
            config_voice
            or request.voice
            or identity.capabilities.default_voice_id
            or "alloy"
        )
        body = {
            "model": identity.model_name,
            "input": request.input_text,
            "voice": voice,
            "response_format": request.config.response_format
            if request.config
            else "mp3",
            "speed": request.config.speed if request.config else 1.0,
        }
        return RequestData(url=url, headers=headers, body=body)

    async def parse_speech_response(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        raw_response: httpx.Response,
    ) -> AudioResponse:
        from zhenxun.services.ai.core.messages import AudioResponse, UsageInfo

        audio_bytes = await raw_response.aread()
        return AudioResponse(
            audio_bytes=audio_bytes,
            audio_format="mp3",
            usage=UsageInfo(),
            model_name=identity.model_name,
        )
