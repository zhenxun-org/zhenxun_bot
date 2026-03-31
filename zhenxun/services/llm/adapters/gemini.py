"""
Gemini API 适配器
"""

from typing import TYPE_CHECKING, Any

from zhenxun.services.log import logger

from ..config.generation import ResponseFormat
from ..types import LLMContentPart
from ..types.exceptions import LLMErrorCode, LLMException
from ..types.models import BasePlatformTool, ToolChoice
from .base import BaseAdapter, RequestData, ResponseData
from .components.gemini_components import (
    GeminiConfigMapper,
    GeminiMessageConverter,
    GeminiResponseParser,
    GeminiToolSerializer,
)

if TYPE_CHECKING:
    from ..config.generation import LLMEmbeddingConfig, LLMGenerationConfig
    from ..service import LLMModel
    from ..types import LLMMessage


class GeminiAdapter(BaseAdapter):
    """Gemini API 适配器"""

    @property
    def log_sanitization_context(self) -> str:
        return "gemini_request"

    @property
    def api_type(self) -> str:
        return "gemini"

    @property
    def supported_api_types(self) -> list[str]:
        return ["gemini"]

    def get_base_headers(
        self, api_key: str, model: "LLMModel | None" = None
    ) -> dict[str, str]:
        """获取基础请求头"""
        from zhenxun.utils.user_agent import get_user_agent

        headers = get_user_agent()
        headers.update({"Content-Type": "application/json"})
        headers["x-goog-api-key"] = api_key
        headers.update(self._get_provider_extra_headers(model))
        return headers

    async def prepare_advanced_request(
        self,
        model: "LLMModel",
        api_key: str,
        messages: list["LLMMessage"],
        config: "LLMGenerationConfig | None" = None,
        tools: list[Any] | None = None,
        tool_choice: str | dict[str, Any] | ToolChoice | None = None,
    ) -> RequestData:
        """准备高级请求"""
        effective_config = config if config is not None else model._generation_config

        if tools:
            from ..types.models import GeminiUrlContext

            context_urls: list[str] = []
            for tool in tools:
                if isinstance(tool, GeminiUrlContext):
                    context_urls.extend(tool.urls)

            if context_urls and messages:
                last_msg = messages[-1]
                if last_msg.role == "user":
                    url_text = "\n\n[Context URLs]:\n" + "\n".join(context_urls)
                    if isinstance(last_msg.content, str):
                        last_msg.content += url_text
                    elif isinstance(last_msg.content, list):
                        last_msg.content.append(LLMContentPart.text_part(url_text))

        has_function_tools = False
        if tools:
            has_function_tools = any(hasattr(tool, "get_definition") for tool in tools)

        is_structured = False
        if effective_config and effective_config.output:
            if (
                effective_config.output.response_schema
                or effective_config.output.response_format == ResponseFormat.JSON
                or effective_config.output.response_mime_type == "application/json"
            ):
                is_structured = True

        if (has_function_tools or is_structured) and effective_config:
            if effective_config.reasoning is None:
                from ..config.generation import ReasoningConfig

                effective_config.reasoning = ReasoningConfig()

            if (
                effective_config.reasoning.budget_tokens is None
                and effective_config.reasoning.effort is None
            ):
                reason_desc = "工具调用" if has_function_tools else "结构化输出"
                logger.debug(
                    f"检测到{reason_desc}，自动为模型 {model.model_name} 开启思维链增强"
                )
                effective_config.reasoning.budget_tokens = -1

        endpoint = self._get_gemini_endpoint(model, effective_config)
        url = self.get_api_url(model, endpoint)
        headers = self.get_base_headers(api_key, model)

        converter = GeminiMessageConverter()
        system_instruction_parts: list[dict[str, Any]] | None = None
        for msg in messages:
            if msg.role == "system":
                if isinstance(msg.content, str):
                    system_instruction_parts = [{"text": msg.content}]
                elif isinstance(msg.content, list):
                    system_instruction_parts = [
                        await converter.convert_part(part) for part in msg.content
                    ]
                continue

        gemini_contents = await converter.convert_messages_async(messages)

        body: dict[str, Any] = {"contents": gemini_contents}

        if system_instruction_parts:
            body["systemInstruction"] = {"parts": system_instruction_parts}

        all_tools_for_request = []
        has_user_functions = False
        if tools:
            from ..types.protocols import ToolExecutable

            function_tools: list[ToolExecutable] = []
            gemini_tools_dict: dict[str, Any] = {}

            for tool in tools:
                if isinstance(tool, BasePlatformTool):
                    declaration = tool.get_tool_declaration()
                    if declaration:
                        gemini_tools_dict.update(declaration)
                elif hasattr(tool, "get_definition"):
                    function_tools.append(tool)

            if function_tools:
                import asyncio

                definition_tasks = [
                    executable.get_definition() for executable in function_tools
                ]
                tool_definitions = await asyncio.gather(*definition_tasks)

                serializer = GeminiToolSerializer()
                function_declarations = serializer.serialize_tools(tool_definitions)

                if function_declarations:
                    gemini_tools_dict["functionDeclarations"] = function_declarations
                    has_user_functions = True

            if gemini_tools_dict:
                all_tools_for_request.append(gemini_tools_dict)

        if all_tools_for_request:
            body["tools"] = all_tools_for_request

        tool_config_updates: dict[str, Any] = {}
        if (
            effective_config
            and effective_config.custom_params
            and "user_location" in effective_config.custom_params
        ):
            tool_config_updates["retrievalConfig"] = {
                "latLng": effective_config.custom_params["user_location"]
            }

        if tool_config_updates:
            body.setdefault("toolConfig", {}).update(tool_config_updates)

        converted_params: dict[str, Any] = {}
        if effective_config:
            converted_params = self.convert_generation_config(effective_config, model)

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

    def apply_config_override(
        self,
        model: "LLMModel",
        body: dict[str, Any],
        config: "LLMGenerationConfig | None" = None,
    ) -> dict[str, Any]:
        """应用配置覆盖 - Gemini 不需要额外的配置覆盖"""
        return body

    def _get_gemini_endpoint(
        self, model: "LLMModel", config: "LLMGenerationConfig | None" = None
    ) -> str:
        """返回Gemini generateContent 端点"""
        return f"/v1beta/models/{model.model_name}:generateContent"

    def parse_response(
        self,
        model: "LLMModel",
        response_json: dict[str, Any],
        is_advanced: bool = False,
    ) -> ResponseData:
        """解析 Gemini API 响应"""
        _ = model, is_advanced
        parser = GeminiResponseParser()
        return parser.parse(response_json)

    def prepare_embedding_request(
        self,
        model: "LLMModel",
        api_key: str,
        texts: list[str],
        config: "LLMEmbeddingConfig",
    ) -> RequestData:
        """准备文本嵌入请求"""
        api_model_name = model.model_name
        if not api_model_name.startswith("models/"):
            api_model_name = f"models/{api_model_name}"

        if not model.api_base:
            raise LLMException(
                f"模型 {model.model_name} 的 api_base 未设置",
                code=LLMErrorCode.CONFIGURATION_ERROR,
            )

        base_url = model.api_base.rstrip("/")
        url = f"{base_url}/v1beta/{api_model_name}:batchEmbedContents"
        headers = self.get_base_headers(api_key, model)

        requests_payload = []
        for text_content in texts:
            safe_text = text_content if text_content else " "
            request_item: dict[str, Any] = {
                "model": api_model_name,
                "content": {"parts": [{"text": safe_text}]},
            }

            if config.task_type:
                request_item["task_type"] = str(config.task_type).upper()
            if config.title:
                request_item["title"] = config.title
            if config.output_dimensionality:
                request_item["output_dimensionality"] = config.output_dimensionality

            requests_payload.append(request_item)

        body = {"requests": requests_payload}
        return RequestData(url=url, headers=headers, body=body)

    def parse_embedding_response(
        self, response_json: dict[str, Any]
    ) -> list[list[float]]:
        """解析文本嵌入响应"""
        try:
            embeddings_data = response_json["embeddings"]
            return [item["values"] for item in embeddings_data]
        except KeyError as e:
            logger.error(f"解析Gemini嵌入响应时缺少键: {e}. 响应: {response_json}")
            raise LLMException(
                "Gemini嵌入响应格式错误",
                code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                details={"error": str(e)},
            )
        except Exception as e:
            logger.error(
                f"解析Gemini嵌入响应时发生未知错误: {e}. 响应: {response_json}"
            )
            raise LLMException(
                f"解析Gemini嵌入响应失败: {e}",
                code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                cause=e,
            )

    def validate_embedding_response(self, response_json: dict[str, Any]) -> None:
        """验证嵌入响应"""
        super().validate_embedding_response(response_json)
        if "embeddings" not in response_json or not isinstance(
            response_json["embeddings"], list
        ):
            raise LLMException(
                "Gemini嵌入响应缺少'embeddings'字段或格式不正确",
                code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                details=response_json,
            )
        for item in response_json["embeddings"]:
            if "values" not in item:
                raise LLMException(
                    "Gemini嵌入响应的条目中缺少'values'字段",
                    code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                    details=response_json,
                )

    def convert_generation_config(
        self, config: "LLMGenerationConfig", model: "LLMModel"
    ) -> dict[str, Any]:
        mapper = GeminiConfigMapper()
        return mapper.map_config(config, model.model_detail, model.capabilities)
