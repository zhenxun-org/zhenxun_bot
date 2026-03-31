"""
OpenAI API 适配器

支持 OpenAI、智谱AI 等 OpenAI 兼容的 API 服务。
"""

from abc import ABC, abstractmethod
import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

import json_repair

from zhenxun.services.llm.config.generation import ImageAspectRatio
from zhenxun.services.llm.types.exceptions import LLMErrorCode, LLMException
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from ..types import StructuredOutputStrategy
from ..types.models import ToolChoice
from ..utils import sanitize_schema_for_llm
from .base import (
    BaseAdapter,
    OpenAICompatAdapter,
    RequestData,
    ResponseData,
    process_image_data,
)
from .components.openai_components import (
    OpenAIConfigMapper,
    OpenAIMessageConverter,
    OpenAIResponseParser,
    OpenAIToolSerializer,
)

if TYPE_CHECKING:
    from ..config.generation import LLMEmbeddingConfig, LLMGenerationConfig
    from ..service import LLMModel
    from ..types import LLMMessage


class APIProtocol(ABC):
    """API 协议策略基类"""

    @abstractmethod
    def build_request_body(
        self,
        model: "LLMModel",
        messages: list["LLMMessage"],
        tools: list[dict[str, Any]] | None,
        tool_choice: Any,
    ) -> dict[str, Any]:
        """构建不同协议下的请求体"""
        pass

    @abstractmethod
    def parse_response(self, response_json: dict[str, Any]) -> ResponseData:
        """解析不同协议下的响应"""
        pass


class StandardProtocol(APIProtocol):
    """标准 OpenAI 协议策略"""

    def __init__(self, adapter: "OpenAICompatAdapter"):
        self.adapter = adapter

    def build_request_body(
        self,
        model: "LLMModel",
        messages: list["LLMMessage"],
        tools: list[dict[str, Any]] | None,
        tool_choice: Any,
    ) -> dict[str, Any]:
        converter = OpenAIMessageConverter()
        openai_messages = converter.convert_messages(messages)
        body: dict[str, Any] = {
            "model": model.model_name,
            "messages": openai_messages,
        }
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        return body

    def parse_response(self, response_json: dict[str, Any]) -> ResponseData:
        parser = OpenAIResponseParser()
        return parser.parse(response_json)


class ResponsesProtocol(APIProtocol):
    """/v1/responses 新版协议策略"""

    def __init__(self, adapter: "OpenAICompatAdapter"):
        self.adapter = adapter

    def build_request_body(
        self,
        model: "LLMModel",
        messages: list["LLMMessage"],
        tools: list[dict[str, Any]] | None,
        tool_choice: Any,
    ) -> dict[str, Any]:
        input_items: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.role
            content_list: list[dict[str, Any]] = []
            raw_contents = (
                msg.content if isinstance(msg.content, list) else [msg.content]
            )

            for part in raw_contents:
                if part is None:
                    continue
                if isinstance(part, str):
                    content_list.append({"type": "input_text", "text": part})
                    continue

                if hasattr(part, "type"):
                    part_type = getattr(part, "type", None)
                    if part_type == "text":
                        content_list.append(
                            {"type": "input_text", "text": getattr(part, "text", "")}
                        )
                    elif part_type == "image":
                        content_list.append(
                            {
                                "type": "input_image",
                                "image_url": getattr(part, "image_source", ""),
                            }
                        )
                    continue

                if isinstance(part, dict):
                    part_type = part.get("type")
                    if part_type == "text":
                        content_list.append(
                            {"type": "input_text", "text": part.get("text", "")}
                        )
                    elif part_type in {"image", "image_url"}:
                        image_src = part.get("image_url") or part.get(
                            "image_source", ""
                        )
                        content_list.append(
                            {
                                "type": "input_image",
                                "image_url": image_src,
                            }
                        )

            input_items.append({"role": role, "content": content_list})

        body: dict[str, Any] = {
            "model": model.model_name,
            "input": input_items,
        }
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        return body

    def parse_response(self, response_json: dict[str, Any]) -> ResponseData:
        self.adapter.validate_response(response_json)
        text_content = ""
        for item in response_json.get("output", []):
            if item.get("type") == "message" and item.get("role") == "assistant":
                for content_item in item.get("content", []):
                    if content_item.get("type") == "output_text":
                        text_content += content_item.get("text", "")

        return ResponseData(
            text=text_content,
            usage_info=response_json.get("usage"),
            raw_response=response_json,
        )


class OpenAIAdapter(OpenAICompatAdapter):
    """OpenAI兼容API适配器"""

    @property
    def api_type(self) -> str:
        return "openai"

    @property
    def supported_api_types(self) -> list[str]:
        return [
            "openai",
            "zhipu",
            "ark",
            "openrouter",
            "openai_responses",
        ]

    def get_chat_endpoint(self, model: "LLMModel") -> str:
        """返回聊天完成端点"""
        if model.model_detail.endpoint:
            return model.model_detail.endpoint

        current_api_type = model.model_detail.api_type or model.api_type

        if current_api_type == "openai_responses":
            return "/v1/responses"
        if current_api_type == "ark":
            return "/api/v3/chat/completions"
        if current_api_type == "zhipu":
            return "/api/paas/v4/chat/completions"
        return "/v1/chat/completions"

    def _get_protocol_strategy(self, model: "LLMModel") -> APIProtocol:
        """根据 API 类型获取对应的处理策略"""
        current_api_type = model.model_detail.api_type or model.api_type
        if current_api_type == "openai_responses":
            return ResponsesProtocol(self)
        return StandardProtocol(self)

    def get_embedding_endpoint(self, model: "LLMModel") -> str:
        """根据API类型返回嵌入端点"""
        if model.api_type == "zhipu":
            return "/v4/embeddings"
        return "/v1/embeddings"

    def convert_generation_config(
        self, config: "LLMGenerationConfig", model: "LLMModel"
    ) -> dict[str, Any]:
        mapper = OpenAIConfigMapper(api_type=self.api_type)
        return mapper.map_config(config, model.model_detail, model.capabilities)

    async def prepare_advanced_request(
        self,
        model: "LLMModel",
        api_key: str,
        messages: list["LLMMessage"],
        config: "LLMGenerationConfig | None" = None,
        tools: list[Any] | None = None,
        tool_choice: str | dict[str, Any] | ToolChoice | None = None,
    ) -> "RequestData":
        """根据不同协议策略构建高级请求"""
        url = self.get_api_url(model, self.get_chat_endpoint(model))
        headers = self.get_base_headers(api_key, model)
        if model.api_type == "openrouter":
            headers.update(
                {
                    "HTTP-Referer": "https://github.com/zhenxun-org/zhenxun_bot",
                    "X-Title": "Zhenxun Bot",
                }
            )

        default_config = getattr(model, "_generation_config", None)
        effective_config = config if config is not None else default_config
        structured_strategy = (
            effective_config.output.structured_output_strategy
            if effective_config and effective_config.output
            else None
        )
        if structured_strategy is None:
            structured_strategy = StructuredOutputStrategy.NATIVE

        openai_tools: list[dict[str, Any]] | None = None
        executables: list[Any] = []
        if tools:
            if isinstance(tools, dict):
                executables = list(tools.values())
            else:
                for tool in tools:
                    if hasattr(tool, "get_definition"):
                        executables.append(tool)

        definition_tasks = [executable.get_definition() for executable in executables]
        tool_defs: list[Any] = []
        if definition_tasks:
            import asyncio

            tool_defs = await asyncio.gather(*definition_tasks)

        if tool_defs:
            serializer = OpenAIToolSerializer()
            openai_tools = serializer.serialize_tools(tool_defs)

        final_tool_choice = tool_choice
        if final_tool_choice is None:
            if (
                effective_config
                and effective_config.tool_config
                and effective_config.tool_config.mode == "ANY"
            ):
                allowed = effective_config.tool_config.allowed_function_names
                if allowed:
                    if len(allowed) == 1:
                        final_tool_choice = {
                            "type": "function",
                            "function": {"name": allowed[0]},
                        }
                    else:
                        logger.warning(
                            "OpenAI API 不支持多个 allowed_function_names，降级为"
                            " required。"
                        )
                        final_tool_choice = "required"
                else:
                    final_tool_choice = "required"

        if (
            structured_strategy == StructuredOutputStrategy.TOOL_CALL
            and effective_config
            and effective_config.output
            and effective_config.output.response_schema
        ):
            sanitized_schema = sanitize_schema_for_llm(
                effective_config.output.response_schema, api_type="openai"
            )
            structured_tool = {
                "type": "function",
                "function": {
                    "name": "return_structured_response",
                    "description": "Return the final structured response.",
                    "parameters": sanitized_schema,
                    "strict": True if model.api_type != "deepseek" else False,
                },
            }
            if openai_tools is None:
                openai_tools = []
            openai_tools.append(structured_tool)
            final_tool_choice = {
                "type": "function",
                "function": {"name": "return_structured_response"},
            }

        protocol_strategy = self._get_protocol_strategy(model)
        body = protocol_strategy.build_request_body(
            model=model,
            messages=messages,
            tools=openai_tools,
            tool_choice=final_tool_choice,
        )

        body = self.apply_config_override(model, body, config)

        if final_tool_choice is not None:
            body["tool_choice"] = final_tool_choice

        response_format = body.get("response_format", {})
        inject_prompt = (
            structured_strategy == StructuredOutputStrategy.NATIVE
            and isinstance(response_format, dict)
            and response_format.get("type") == "json_object"
        )

        if inject_prompt:
            messages_list = body.get("messages", [])
            has_json_keyword = False
            for msg in messages_list:
                content = msg.get("content")
                if isinstance(content, str) and "json" in content.lower():
                    has_json_keyword = True
                    break
                if isinstance(content, list):
                    for part in content:
                        if (
                            isinstance(part, dict)
                            and part.get("type") == "text"
                            and "json" in part.get("text", "").lower()
                        ):
                            has_json_keyword = True
                            break
                    if has_json_keyword:
                        break

            if not has_json_keyword:
                injection_text = (
                    "请务必输出合法的 JSON 格式，避免额外的文本、Markdown 或解释。"
                )
                system_msg = next(
                    (m for m in messages_list if m.get("role") == "system"), None
                )
                if system_msg:
                    if isinstance(system_msg.get("content"), str):
                        system_msg["content"] += " " + injection_text
                    elif isinstance(system_msg.get("content"), list):
                        system_msg["content"].append(
                            {"type": "text", "text": injection_text}
                        )
                else:
                    messages_list.insert(
                        0, {"role": "system", "content": injection_text}
                    )
                body["messages"] = messages_list

        return RequestData(url=url, headers=headers, body=body)

    def parse_response(
        self,
        model: "LLMModel",
        response_json: dict[str, Any],
        is_advanced: bool = False,
    ) -> ResponseData:
        """解析响应 - 使用策略模式委托处理"""
        _ = is_advanced
        protocol_strategy = self._get_protocol_strategy(model)
        response_data = protocol_strategy.parse_response(response_json)

        if response_data.tool_calls:
            target_tool = next(
                (
                    tc
                    for tc in response_data.tool_calls
                    if tc.function.name == "return_structured_response"
                ),
                None,
            )
            if target_tool:
                response_data.text = json_repair.repair_json(
                    target_tool.function.arguments
                )
                remaining = [
                    tc
                    for tc in response_data.tool_calls
                    if tc.function.name != "return_structured_response"
                ]
                response_data.tool_calls = remaining or None

        return response_data


class DeepSeekAdapter(OpenAIAdapter):
    """DeepSeek 专用适配器 (基于 OpenAI 协议)"""

    @property
    def api_type(self) -> str:
        return "deepseek"

    @property
    def supported_api_types(self) -> list[str]:
        return ["deepseek"]


class OpenAIImageAdapter(BaseAdapter):
    """OpenAI 图像生成/编辑适配器"""

    @property
    def api_type(self) -> str:
        return "openai_image"

    @property
    def log_sanitization_context(self) -> str:
        return "openai_request"

    @property
    def supported_api_types(self) -> list[str]:
        return ["openai_image", "nano_banana"]

    async def prepare_advanced_request(
        self,
        model: "LLMModel",
        api_key: str,
        messages: list["LLMMessage"],
        config: "LLMGenerationConfig | None" = None,
        tools: list[Any] | None = None,
        tool_choice: "str | dict[str, Any] | ToolChoice | None" = None,
    ) -> RequestData:
        _ = tools, tool_choice
        effective_config = config if config is not None else model._generation_config
        headers = self.get_base_headers(api_key, model)

        prompt = ""
        images_bytes_list: list[bytes] = []

        for msg in reversed(messages):
            if msg.role != "user":
                continue
            if isinstance(msg.content, str):
                prompt = msg.content
            elif isinstance(msg.content, list):
                for part in msg.content:
                    if part.type == "text" and not prompt:
                        prompt = part.text
                    elif part.type == "image":
                        if part.is_image_base64():
                            if b64_data := part.get_base64_data():
                                _, b64_str = b64_data
                                images_bytes_list.append(base64.b64decode(b64_str))
                        elif part.is_image_url() and part.image_source:
                            images_bytes_list.append(
                                await AsyncHttpx.get_content(part.image_source)
                            )
            if prompt:
                break

        if not prompt and not images_bytes_list:
            raise LLMException(
                "图像生成需要提供 Prompt",
                code=LLMErrorCode.CONFIGURATION_ERROR,
            )

        body: dict[str, Any] = {
            "model": model.model_name,
            "prompt": prompt,
            "response_format": "b64_json",
        }

        if effective_config:
            if effective_config.visual:
                if effective_config.visual.aspect_ratio:
                    ar = effective_config.visual.aspect_ratio
                    size_map = {
                        ImageAspectRatio.SQUARE: "1024x1024",
                        ImageAspectRatio.LANDSCAPE_16_9: "1792x1024",
                        ImageAspectRatio.PORTRAIT_9_16: "1024x1792",
                    }
                    if isinstance(ar, ImageAspectRatio) and ar in size_map:
                        body["size"] = size_map[ar]
                        body["aspect_ratio"] = ar.value
                    elif isinstance(ar, str):
                        if "x" in ar:
                            body["size"] = ar
                        else:
                            body["aspect_ratio"] = ar

                if effective_config.visual.resolution:
                    res_val = effective_config.visual.resolution
                    if not isinstance(res_val, str):
                        res_val = getattr(res_val, "value", res_val)
                    body["image_size"] = res_val

            if effective_config.custom_params:
                body.update(effective_config.custom_params)

        if images_bytes_list:
            b64_images = []
            for img_bytes in images_bytes_list:
                b64_str = base64.b64encode(img_bytes).decode("utf-8")
                b64_images.append(b64_str)
            body["image"] = b64_images

        endpoint = "/v1/images/generations"
        url = self.get_api_url(model, endpoint)
        return RequestData(url=url, headers=headers, body=body)

    def parse_response(
        self,
        model: "LLMModel",
        response_json: dict[str, Any],
        is_advanced: bool = False,
    ) -> ResponseData:
        _ = model, is_advanced
        self.validate_response(response_json)

        images_data: list[bytes | Path] = []
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
                logger.warning(
                    f"API 返回了 URL 而不是 Base64: {item.get('url', 'unknown')}"
                )

        text_summary = (
            f"已生成 {len(images_data)} 张图片。"
            if images_data
            else "图像生成接口调用成功，但未解析到图片数据。"
        )

        return ResponseData(
            text=text_summary,
            images=images_data if images_data else None,
            raw_response=response_json,
        )

    def prepare_embedding_request(
        self,
        model: "LLMModel",
        api_key: str,
        texts: list[str],
        config: "LLMEmbeddingConfig",
    ) -> RequestData:
        raise NotImplementedError("OpenAIImageAdapter 不支持 Embedding")

    def parse_embedding_response(
        self, response_json: dict[str, Any]
    ) -> list[list[float]]:
        raise NotImplementedError("OpenAIImageAdapter 不支持 Embedding")

    def convert_generation_config(
        self, config: "LLMGenerationConfig", model: "LLMModel"
    ) -> dict[str, Any]:
        _ = config, model
        return {}
