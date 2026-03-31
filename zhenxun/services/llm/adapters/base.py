"""
LLM 适配器基类和通用数据结构
"""

from abc import ABC, abstractmethod
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

import httpx
from pydantic import BaseModel

from zhenxun.configs.path_config import TEMP_PATH
from zhenxun.services.log import logger

from ..types import LLMContentPart
from ..types.exceptions import LLMErrorCode, LLMException
from ..types.models import LLMToolCall

if TYPE_CHECKING:
    from ..config.generation import LLMEmbeddingConfig, LLMGenerationConfig
    from ..service import LLMModel
    from ..types import LLMMessage
    from ..types.models import ToolChoice


class RequestData(BaseModel):
    """请求数据封装"""

    url: str
    headers: dict[str, str]
    body: dict[str, Any]
    files: dict[str, Any] | list[tuple[str, Any]] | None = None


class ResponseData(BaseModel):
    """响应数据封装 - 支持所有高级功能"""

    text: str
    content_parts: list[LLMContentPart] | None = None
    images: list[bytes | Path] | None = None
    usage_info: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    tool_calls: list[LLMToolCall] | None = None
    code_executions: list[Any] | None = None
    grounding_metadata: Any | None = None
    cache_info: Any | None = None
    thought_text: str | None = None
    thought_signature: str | None = None

    code_execution_results: list[dict[str, Any]] | None = None
    search_results: list[dict[str, Any]] | None = None
    function_calls: list[dict[str, Any]] | None = None
    safety_ratings: list[dict[str, Any]] | None = None
    citations: list[dict[str, Any]] | None = None


def process_image_data(image_data: bytes) -> bytes | Path:
    """
    处理图片数据：若超过 2MB 则保存到临时目录，避免占用内存。
    """
    max_inline_size = 2 * 1024 * 1024
    if len(image_data) > max_inline_size:
        save_dir = TEMP_PATH / "llm"
        save_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{uuid.uuid4()}.png"
        file_path = save_dir / file_name
        file_path.write_bytes(image_data)
        logger.info(
            f"图片数据过大 ({len(image_data)} bytes)，已保存到临时文件: {file_path}",
            "LLMAdapter",
        )
        return file_path.resolve()
    return image_data


class BaseAdapter(ABC):
    """LLM API适配器基类"""

    @property
    def log_sanitization_context(self) -> str:
        """用于日志清洗的上下文名称，默认 'default'"""
        return "default"

    @property
    @abstractmethod
    def api_type(self) -> str:
        """API类型标识"""
        pass

    @property
    @abstractmethod
    def supported_api_types(self) -> list[str]:
        """支持的API类型列表"""
        pass

    async def prepare_simple_request(
        self,
        model: "LLMModel",
        api_key: str,
        prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> RequestData:
        """准备简单文本生成请求

        默认实现：将简单请求转换为高级请求格式
        子类可以重写此方法以提供特定的优化实现
        """
        from ..types import LLMMessage

        messages: list[LLMMessage] = []

        if history:
            for msg in history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                messages.append(LLMMessage(role=role, content=content))

        messages.append(LLMMessage(role="user", content=prompt))

        config = model._generation_config

        return await self.prepare_advanced_request(
            model=model,
            api_key=api_key,
            messages=messages,
            config=config,
            tools=None,
            tool_choice=None,
        )

    @abstractmethod
    async def prepare_advanced_request(
        self,
        model: "LLMModel",
        api_key: str,
        messages: list["LLMMessage"],
        config: "LLMGenerationConfig | None" = None,
        tools: list[Any] | None = None,
        tool_choice: "str | dict[str, Any] | ToolChoice | None" = None,
    ) -> RequestData:
        """准备高级请求"""
        pass

    @abstractmethod
    def parse_response(
        self,
        model: "LLMModel",
        response_json: dict[str, Any],
        is_advanced: bool = False,
    ) -> ResponseData:
        """解析API响应"""
        pass

    @abstractmethod
    def prepare_embedding_request(
        self,
        model: "LLMModel",
        api_key: str,
        texts: list[str],
        config: "LLMEmbeddingConfig",
    ) -> RequestData:
        """准备文本嵌入请求"""
        pass

    @abstractmethod
    def parse_embedding_response(
        self, response_json: dict[str, Any]
    ) -> list[list[float]]:
        """解析文本嵌入响应"""
        pass

    @abstractmethod
    def convert_generation_config(
        self, config: "LLMGenerationConfig", model: "LLMModel"
    ) -> dict[str, Any]:
        """将通用生成配置转换为特定API的参数字典"""
        pass

    def validate_embedding_response(self, response_json: dict[str, Any]) -> None:
        """验证嵌入API响应"""
        if response_json.get("error"):
            error_info = response_json["error"]
            msg = (
                error_info.get("message", str(error_info))
                if isinstance(error_info, dict)
                else str(error_info)
            )
            raise LLMException(
                f"嵌入API错误: {msg}",
                code=LLMErrorCode.EMBEDDING_FAILED,
                details=response_json,
            )

    def get_api_url(self, model: "LLMModel", endpoint: str) -> str:
        """构建API URL"""
        if not model.api_base:
            raise LLMException(
                f"模型 {model.model_name} 的 api_base 未设置",
                code=LLMErrorCode.CONFIGURATION_ERROR,
            )
        return f"{model.api_base.rstrip('/')}{endpoint}"

    def _get_provider_extra_headers(self, model: "LLMModel | None") -> dict[str, str]:
        if not model:
            return {}
        raw_headers = getattr(model.provider_config, "extra_headers", None)
        if not isinstance(raw_headers, dict):
            return {}
        headers: dict[str, str] = {}
        for key, value in raw_headers.items():
            key_text = str(key).strip()
            if not key_text or value is None:
                continue
            headers[key_text] = str(value)
        return headers

    def get_base_headers(
        self, api_key: str, model: "LLMModel | None" = None
    ) -> dict[str, str]:
        """获取基础请求头"""
        from zhenxun.utils.user_agent import get_user_agent

        headers = get_user_agent()
        headers.update(
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
        )
        headers.update(self._get_provider_extra_headers(model))
        return headers

    def validate_response(self, response_json: dict[str, Any]) -> None:
        """验证API响应，解析不同API的错误结构"""
        if response_json.get("error"):
            error_info = response_json["error"]

            if isinstance(error_info, dict):
                error_message = error_info.get("message", "未知错误")
                error_code = error_info.get("code", "unknown")
                error_type = error_info.get("type", "api_error")

                error_code_mapping = {
                    "invalid_api_key": LLMErrorCode.API_KEY_INVALID,
                    "authentication_failed": LLMErrorCode.API_KEY_INVALID,
                    "insufficient_quota": LLMErrorCode.API_QUOTA_EXCEEDED,
                    "rate_limit_exceeded": LLMErrorCode.API_RATE_LIMITED,
                    "quota_exceeded": LLMErrorCode.API_RATE_LIMITED,
                    "model_not_found": LLMErrorCode.MODEL_NOT_FOUND,
                    "invalid_model": LLMErrorCode.MODEL_NOT_FOUND,
                    "context_length_exceeded": LLMErrorCode.CONTEXT_LENGTH_EXCEEDED,
                    "max_tokens_exceeded": LLMErrorCode.CONTEXT_LENGTH_EXCEEDED,
                    "invalid_request_error": LLMErrorCode.INVALID_PARAMETER,
                    "invalid_parameter": LLMErrorCode.INVALID_PARAMETER,
                }

                llm_error_code = error_code_mapping.get(
                    error_code, LLMErrorCode.API_RESPONSE_INVALID
                )

                logger.error(
                    f"API返回错误: {error_message} "
                    f"(代码: {error_code}, 类型: {error_type})"
                )
            else:
                error_message = str(error_info)
                error_code = "unknown"
                llm_error_code = LLMErrorCode.API_RESPONSE_INVALID

                logger.error(f"API返回错误: {error_message}")

            raise LLMException(
                f"API请求失败: {error_message}",
                code=llm_error_code,
                details={"api_error": error_info, "error_code": error_code},
            )

        if "candidates" in response_json:
            candidates = response_json.get("candidates", [])
            if candidates:
                candidate = candidates[0]
                finish_reason = candidate.get("finishReason")
                if finish_reason in ["SAFETY", "RECITATION"]:
                    safety_ratings = candidate.get("safetyRatings", [])
                    logger.warning(
                        f"Gemini内容被安全过滤: {finish_reason}, "
                        f"安全评级: {safety_ratings}"
                    )
                    raise LLMException(
                        f"内容被安全过滤: {finish_reason}",
                        code=LLMErrorCode.CONTENT_FILTERED,
                        details={
                            "finish_reason": finish_reason,
                            "safety_ratings": safety_ratings,
                        },
                    )

        if not response_json:
            logger.error("API返回空响应")
            raise LLMException(
                "API返回空响应",
                code=LLMErrorCode.API_RESPONSE_INVALID,
                details={"response": response_json},
            )

    def _apply_generation_config(
        self,
        model: "LLMModel",
        config: "LLMGenerationConfig | None" = None,
    ) -> dict[str, Any]:
        """通用的配置应用逻辑"""
        if config is not None:
            return self.convert_generation_config(config, model)

        if model._generation_config:
            return self.convert_generation_config(model._generation_config, model)

        return {}

    def apply_config_override(
        self,
        model: "LLMModel",
        body: dict[str, Any],
        config: "LLMGenerationConfig | None" = None,
    ) -> dict[str, Any]:
        """应用配置覆盖"""
        config_params = self._apply_generation_config(model, config)
        body.update(config_params)
        return body

    def handle_http_error(self, response: httpx.Response) -> LLMException | None:
        """
        处理 HTTP 错误响应。
        如果响应状态码表示成功 (200)，返回 None；否则构造 LLMException 供外部捕获。
        """
        if response.status_code == 200:
            return None

        error_text = response.content.decode("utf-8", errors="ignore")
        error_status = ""
        error_msg = error_text
        try:
            error_json = json.loads(error_text)
            if isinstance(error_json, dict) and "error" in error_json:
                error_info = error_json["error"]
                if isinstance(error_info, dict):
                    error_msg = error_info.get("message", error_msg)
                    raw_status = error_info.get("status") or error_info.get("code")
                    error_status = str(raw_status) if raw_status is not None else ""
                elif error_info is not None:
                    error_msg = str(error_info)
                    error_status = error_msg
        except Exception:
            pass

        status_upper = error_status.upper() if error_status else ""
        text_upper = error_text.upper()

        error_code = LLMErrorCode.API_REQUEST_FAILED
        if response.status_code == 400:
            if (
                "FAILED_PRECONDITION" in status_upper
                or "LOCATION IS NOT SUPPORTED" in text_upper
            ):
                error_code = LLMErrorCode.USER_LOCATION_NOT_SUPPORTED
            elif "INVALID_ARGUMENT" in status_upper:
                error_code = LLMErrorCode.INVALID_PARAMETER
            elif "API_KEY_INVALID" in text_upper or "API KEY NOT VALID" in text_upper:
                error_code = LLMErrorCode.API_KEY_INVALID
            else:
                error_code = LLMErrorCode.INVALID_PARAMETER
        elif response.status_code in [401, 403]:
            if error_msg and (
                "country" in error_msg.lower()
                or "region" in error_msg.lower()
                or "unsupported" in error_msg.lower()
            ):
                error_code = LLMErrorCode.USER_LOCATION_NOT_SUPPORTED
            elif "PERMISSION_DENIED" in status_upper:
                error_code = LLMErrorCode.API_KEY_INVALID
            else:
                error_code = LLMErrorCode.API_KEY_INVALID
        elif response.status_code == 404:
            error_code = LLMErrorCode.MODEL_NOT_FOUND
        elif response.status_code == 429:
            if (
                "RESOURCE_EXHAUSTED" in status_upper
                or "INSUFFICIENT_QUOTA" in status_upper
                or ("quota" in error_msg.lower() if error_msg else False)
            ):
                error_code = LLMErrorCode.API_QUOTA_EXCEEDED
            else:
                error_code = LLMErrorCode.API_RATE_LIMITED
        elif response.status_code in [402, 413]:
            error_code = LLMErrorCode.API_QUOTA_EXCEEDED
        elif response.status_code == 422:
            error_code = LLMErrorCode.GENERATION_FAILED
        elif response.status_code >= 500:
            error_code = LLMErrorCode.API_TIMEOUT

        return LLMException(
            f"HTTP请求失败: {response.status_code} ({error_status or 'Unknown'})",
            code=error_code,
            details={
                "status_code": response.status_code,
                "api_status": error_status,
                "response": error_text,
            },
        )


class OpenAICompatAdapter(BaseAdapter):
    """
    处理所有 OpenAI 兼容 API 的通用适配器。
    """

    @property
    def log_sanitization_context(self) -> str:
        return "openai_request"

    @abstractmethod
    def get_chat_endpoint(self, model: "LLMModel") -> str:
        """子类必须实现，返回 chat completions 的端点"""
        pass

    @abstractmethod
    def get_embedding_endpoint(self, model: "LLMModel") -> str:
        """子类必须实现，返回 embeddings 的端点"""
        pass

    async def prepare_simple_request(
        self,
        model: "LLMModel",
        api_key: str,
        prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> RequestData:
        """准备简单文本生成请求 - OpenAI兼容API的通用实现"""
        url = self.get_api_url(model, self.get_chat_endpoint(model))
        headers = self.get_base_headers(api_key, model)

        messages = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": model.model_name,
            "messages": messages,
        }

        body = self.apply_config_override(model, body)

        return RequestData(url=url, headers=headers, body=body)

    async def prepare_advanced_request(
        self,
        model: "LLMModel",
        api_key: str,
        messages: list["LLMMessage"],
        config: "LLMGenerationConfig | None" = None,
        tools: list[Any] | None = None,
        tool_choice: "str | dict[str, Any] | ToolChoice | None" = None,
    ) -> RequestData:
        """准备高级请求 - OpenAI兼容格式"""
        url = self.get_api_url(model, self.get_chat_endpoint(model))
        headers = self.get_base_headers(api_key, model)
        if model.api_type == "openrouter":
            headers.update(
                {
                    "HTTP-Referer": "https://github.com/zhenxun-org/zhenxun_bot",
                    "X-Title": "Zhenxun Bot",
                }
            )
        from .components.openai_components import OpenAIMessageConverter

        converter = OpenAIMessageConverter()
        openai_messages = converter.convert_messages(messages)

        body = {
            "model": model.model_name,
            "messages": openai_messages,
        }

        openai_tools: list[dict[str, Any]] | None = None
        executables: list[Any] = []
        if tools:
            for tool in tools:
                if hasattr(tool, "get_definition"):
                    executables.append(tool)

        if executables:
            import asyncio

            from zhenxun.utils.pydantic_compat import model_dump

            definition_tasks = [
                executable.get_definition() for executable in executables
            ]
            tool_defs = []
            if definition_tasks:
                tool_defs = await asyncio.gather(*definition_tasks)

            if tool_defs:
                openai_tools = [
                    {"type": "function", "function": model_dump(tool)}
                    for tool in tool_defs
                ]

        if openai_tools:
            body["tools"] = openai_tools

        if tool_choice:
            body["tool_choice"] = tool_choice

        body = self.apply_config_override(model, body, config)
        return RequestData(url=url, headers=headers, body=body)

    def parse_response(
        self,
        model: "LLMModel",
        response_json: dict[str, Any],
        is_advanced: bool = False,
    ) -> ResponseData:
        """解析响应 - 直接使用组件化 ResponseParser"""
        _ = model, is_advanced
        from .components.openai_components import OpenAIResponseParser

        parser = OpenAIResponseParser()
        return parser.parse(response_json)

    def prepare_embedding_request(
        self,
        model: "LLMModel",
        api_key: str,
        texts: list[str],
        config: "LLMEmbeddingConfig",
    ) -> RequestData:
        """准备嵌入请求 - OpenAI兼容格式"""
        url = self.get_api_url(model, self.get_embedding_endpoint(model))
        headers = self.get_base_headers(api_key, model)

        body = {
            "model": model.model_name,
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
        self, response_json: dict[str, Any]
    ) -> list[list[float]]:
        """解析嵌入响应 - OpenAI兼容格式"""
        self.validate_embedding_response(response_json)

        try:
            data = response_json.get("data", [])
            if not data:
                raise LLMException(
                    "嵌入响应中没有数据",
                    code=LLMErrorCode.EMBEDDING_FAILED,
                    details=response_json,
                )

            embeddings = []
            for item in data:
                if "embedding" in item:
                    embeddings.append(item["embedding"])
                else:
                    raise LLMException(
                        "嵌入响应格式错误：缺少embedding字段",
                        code=LLMErrorCode.EMBEDDING_FAILED,
                        details=item,
                    )

            return embeddings

        except Exception as e:
            logger.error(f"解析嵌入响应失败: {e}", e=e)
            raise LLMException(
                f"解析嵌入响应失败: {e}",
                code=LLMErrorCode.EMBEDDING_FAILED,
                cause=e,
            )
