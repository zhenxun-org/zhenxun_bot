"""
LLM 适配器基类和通用数据结构
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from zhenxun.services.log import logger

from ..types.exceptions import LLMErrorCode, LLMException
from ..types.models import LLMToolCall

if TYPE_CHECKING:
    from ..config.generation import LLMGenerationConfig
    from ..service import LLMModel
    from ..types.content import LLMMessage
    from ..types.enums import EmbeddingTaskType
    from ..types.models import LLMTool


class RequestData(BaseModel):
    """请求数据封装"""

    url: str
    headers: dict[str, str]
    body: dict[str, Any]


class ResponseData(BaseModel):
    """响应数据封装 - 支持所有高级功能"""

    text: str
    usage_info: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    tool_calls: list[LLMToolCall] | None = None
    code_executions: list[Any] | None = None
    grounding_metadata: Any | None = None
    cache_info: Any | None = None

    code_execution_results: list[dict[str, Any]] | None = None
    search_results: list[dict[str, Any]] | None = None
    function_calls: list[dict[str, Any]] | None = None
    safety_ratings: list[dict[str, Any]] | None = None
    citations: list[dict[str, Any]] | None = None


class BaseAdapter(ABC):
    """LLM API适配器基类"""

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
        from ..types.content import LLMMessage

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
        tools: list["LLMTool"] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
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
        task_type: "EmbeddingTaskType | str",
        **kwargs: Any,
    ) -> RequestData:
        """准备文本嵌入请求"""
        pass

    @abstractmethod
    def parse_embedding_response(
        self, response_json: dict[str, Any]
    ) -> list[list[float]]:
        """解析文本嵌入响应"""
        pass

    def validate_embedding_response(self, response_json: dict[str, Any]) -> None:
        """验证嵌入API响应"""
        if "error" in response_json:
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

    def get_base_headers(self, api_key: str) -> dict[str, str]:
        """获取基础请求头"""
        from zhenxun.utils.user_agent import get_user_agent

        headers = get_user_agent()
        headers.update(
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
        )
        return headers

    def convert_messages_to_openai_format(
        self, messages: list["LLMMessage"]
    ) -> list[dict[str, Any]]:
        """将LLMMessage转换为OpenAI格式 - 通用方法"""
        openai_messages: list[dict[str, Any]] = []
        for msg in messages:
            openai_msg: dict[str, Any] = {"role": msg.role}

            if msg.role == "tool":
                openai_msg["tool_call_id"] = msg.tool_call_id
                openai_msg["name"] = msg.name
                openai_msg["content"] = msg.content
            else:
                if isinstance(msg.content, str):
                    openai_msg["content"] = msg.content
                else:
                    content_parts = []
                    for part in msg.content:
                        if part.type == "text":
                            content_parts.append({"type": "text", "text": part.text})
                        elif part.type == "image":
                            content_parts.append(
                                {
                                    "type": "image_url",
                                    "image_url": {"url": part.image_source},
                                }
                            )
                    openai_msg["content"] = content_parts

            if msg.role == "assistant" and msg.tool_calls:
                assistant_tool_calls = []
                for call in msg.tool_calls:
                    assistant_tool_calls.append(
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                    )
                openai_msg["tool_calls"] = assistant_tool_calls

            if msg.name and msg.role != "tool":
                openai_msg["name"] = msg.name

            openai_messages.append(openai_msg)
        return openai_messages

    def parse_openai_response(self, response_json: dict[str, Any]) -> ResponseData:
        """解析OpenAI格式的响应 - 通用方法"""
        self.validate_response(response_json)

        try:
            choices = response_json.get("choices", [])
            if not choices:
                logger.debug("OpenAI响应中没有choices，可能为空回复或流结束。")
                return ResponseData(text="", raw_response=response_json)

            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content", "")

            if content:
                content = content.strip()

            parsed_tool_calls: list[LLMToolCall] | None = None
            if message_tool_calls := message.get("tool_calls"):
                from ..types.models import LLMToolFunction

                parsed_tool_calls = []
                for tc_data in message_tool_calls:
                    try:
                        if tc_data.get("type") == "function":
                            parsed_tool_calls.append(
                                LLMToolCall(
                                    id=tc_data["id"],
                                    function=LLMToolFunction(
                                        name=tc_data["function"]["name"],
                                        arguments=tc_data["function"]["arguments"],
                                    ),
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
                if not parsed_tool_calls:
                    parsed_tool_calls = None

            final_text = content if content is not None else ""
            if not final_text and parsed_tool_calls:
                final_text = f"请求调用 {len(parsed_tool_calls)} 个工具。"

            usage_info = response_json.get("usage")

            return ResponseData(
                text=final_text,
                tool_calls=parsed_tool_calls,
                usage_info=usage_info,
                raw_response=response_json,
            )

        except Exception as e:
            logger.error(f"解析OpenAI格式响应失败: {e}", e=e)
            raise LLMException(
                f"解析API响应失败: {e}",
                code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                cause=e,
            )

    def validate_response(self, response_json: dict[str, Any]) -> None:
        """验证API响应，解析不同API的错误结构"""
        if "error" in response_json:
            error_info = response_json["error"]

            if isinstance(error_info, dict):
                error_message = error_info.get("message", "未知错误")
                error_code = error_info.get("code", "unknown")
                error_type = error_info.get("type", "api_error")

                error_code_mapping = {
                    "invalid_api_key": LLMErrorCode.API_KEY_INVALID,
                    "authentication_failed": LLMErrorCode.API_KEY_INVALID,
                    "rate_limit_exceeded": LLMErrorCode.API_RATE_LIMITED,
                    "quota_exceeded": LLMErrorCode.API_RATE_LIMITED,
                    "model_not_found": LLMErrorCode.MODEL_NOT_FOUND,
                    "invalid_model": LLMErrorCode.MODEL_NOT_FOUND,
                    "context_length_exceeded": LLMErrorCode.CONTEXT_LENGTH_EXCEEDED,
                    "max_tokens_exceeded": LLMErrorCode.CONTEXT_LENGTH_EXCEEDED,
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
            return config.to_api_params(model.api_type, model.model_name)

        if model._generation_config is not None:
            return model._generation_config.to_api_params(
                model.api_type, model.model_name
            )

        base_config = {}
        if model.temperature is not None:
            base_config["temperature"] = model.temperature
        if model.max_tokens is not None:
            if model.api_type == "gemini":
                base_config["maxOutputTokens"] = model.max_tokens
            else:
                base_config["max_tokens"] = model.max_tokens

        return base_config

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


class OpenAICompatAdapter(BaseAdapter):
    """
    处理所有 OpenAI 兼容 API 的通用适配器。
    消除 OpenAIAdapter 和 ZhipuAdapter 之间的代码重复。
    """

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
        headers = self.get_base_headers(api_key)

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
        tools: list["LLMTool"] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> RequestData:
        """准备高级请求 - OpenAI兼容格式"""
        url = self.get_api_url(model, self.get_chat_endpoint(model))
        headers = self.get_base_headers(api_key)
        openai_messages = self.convert_messages_to_openai_format(messages)

        body = {
            "model": model.model_name,
            "messages": openai_messages,
        }

        if tools:
            openai_tools = []
            for tool in tools:
                if tool.type == "function" and tool.function:
                    openai_tools.append({"type": "function", "function": tool.function})
                elif tool.type == "mcp" and tool.mcp_session:
                    if callable(tool.mcp_session):
                        raise ValueError(
                            "适配器接收到未激活的 MCP 会话工厂。"
                            "会话工厂应该在 LLMModel.generate_response 中被激活。"
                        )
                    openai_tools.append(
                        tool.mcp_session.to_api_tool(api_type=self.api_type)
                    )
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
        """解析响应 - 直接使用基类的 OpenAI 格式解析"""
        _ = model, is_advanced
        return self.parse_openai_response(response_json)

    def prepare_embedding_request(
        self,
        model: "LLMModel",
        api_key: str,
        texts: list[str],
        task_type: "EmbeddingTaskType | str",
        **kwargs: Any,
    ) -> RequestData:
        """准备嵌入请求 - OpenAI兼容格式"""
        _ = task_type
        url = self.get_api_url(model, self.get_embedding_endpoint(model))
        headers = self.get_base_headers(api_key)

        body = {
            "model": model.model_name,
            "input": texts,
        }

        if kwargs:
            body.update(kwargs)

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
