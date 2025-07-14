"""
LLM 服务 - 会话客户端

提供一个有状态的、面向会话的 LLM 客户端，用于进行多轮对话和复杂交互。
"""

import copy
from dataclasses import dataclass
from typing import Any

from nonebot_plugin_alconna.uniseg import UniMessage

from zhenxun.services.log import logger

from .config import CommonOverrides, LLMGenerationConfig
from .config.providers import get_ai_config
from .manager import get_global_default_model_name, get_model_instance
from .tools import tool_registry
from .types import (
    EmbeddingTaskType,
    LLMContentPart,
    LLMErrorCode,
    LLMException,
    LLMMessage,
    LLMResponse,
    LLMTool,
    ModelName,
)
from .utils import unimsg_to_llm_parts


@dataclass
class AIConfig:
    """AI配置类 - 简化版本"""

    model: ModelName = None
    default_embedding_model: ModelName = None
    temperature: float | None = None
    max_tokens: int | None = None
    enable_cache: bool = False
    enable_code: bool = False
    enable_search: bool = False
    timeout: int | None = None

    enable_gemini_json_mode: bool = False
    enable_gemini_thinking: bool = False
    enable_gemini_safe_mode: bool = False
    enable_gemini_multimodal: bool = False
    enable_gemini_grounding: bool = False
    default_preserve_media_in_history: bool = False

    def __post_init__(self):
        """初始化后从配置中读取默认值"""
        ai_config = get_ai_config()
        if self.model is None:
            self.model = ai_config.get("default_model_name")
        if self.timeout is None:
            self.timeout = ai_config.get("timeout", 180)


class AI:
    """统一的AI服务类 - 平衡设计版本

    提供三层API：
    1. 简单方法：ai.chat(), ai.code(), ai.search()
    2. 标准方法：ai.analyze() 支持复杂参数
    3. 高级方法：通过get_model_instance()直接访问
    """

    def __init__(
        self, config: AIConfig | None = None, history: list[LLMMessage] | None = None
    ):
        """
        初始化AI服务

        参数:
            config: AI 配置.
            history: 可选的初始对话历史.
        """
        self.config = config or AIConfig()
        self.history = history or []

    def clear_history(self):
        """清空当前会话的历史记录"""
        self.history = []
        logger.info("AI session history cleared.")

    def _sanitize_message_for_history(self, message: LLMMessage) -> LLMMessage:
        """
        净化用于存入历史记录的消息。
        将非文本的多模态内容部分替换为文本占位符，以避免重复处理。
        """
        if not isinstance(message.content, list):
            return message

        sanitized_message = copy.deepcopy(message)
        content_list = sanitized_message.content
        if not isinstance(content_list, list):
            return sanitized_message

        new_content_parts: list[LLMContentPart] = []
        has_multimodal_content = False

        for part in content_list:
            if isinstance(part, LLMContentPart) and part.type == "text":
                new_content_parts.append(part)
            else:
                has_multimodal_content = True

        if has_multimodal_content:
            placeholder = "[用户发送了媒体文件，内容已在首次分析时处理]"
            text_part_found = False
            for part in new_content_parts:
                if part.type == "text":
                    part.text = f"{placeholder} {part.text or ''}".strip()
                    text_part_found = True
                    break
            if not text_part_found:
                new_content_parts.insert(0, LLMContentPart.text_part(placeholder))

        sanitized_message.content = new_content_parts
        return sanitized_message

    async def chat(
        self,
        message: str | LLMMessage | list[LLMContentPart],
        *,
        model: ModelName = None,
        preserve_media_in_history: bool | None = None,
        tools: list[LLMTool] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        进行一次聊天对话，支持工具调用。
        此方法会自动使用和更新会话内的历史记录。

        参数:
            message: 用户输入的消息。
            model: 本次对话要使用的模型。
            preserve_media_in_history: 是否在历史记录中保留原始多模态信息。
                - True: 保留，用于深度多轮媒体分析。
                - False: 不保留，替换为占位符，提高效率。
                - None (默认): 使用AI实例配置的默认值。
            tools: 本次对话可用的工具列表。
            tool_choice: 强制模型使用的工具。
            **kwargs: 传递给模型的其他生成参数。

        返回:
            LLMResponse: 模型的完整响应，可能包含文本或工具调用请求。
        """
        current_message: LLMMessage
        if isinstance(message, str):
            current_message = LLMMessage.user(message)
        elif isinstance(message, list) and all(
            isinstance(part, LLMContentPart) for part in message
        ):
            current_message = LLMMessage.user(message)
        elif isinstance(message, LLMMessage):
            current_message = message
        else:
            raise LLMException(
                f"AI.chat 不支持的消息类型: {type(message)}. "
                "请使用 str, LLMMessage, 或 list[LLMContentPart]. "
                "对于更复杂的多模态输入或文件路径，请使用 AI.analyze().",
                code=LLMErrorCode.API_REQUEST_FAILED,
            )

        final_messages = [*self.history, current_message]

        response = await self._execute_generation(
            messages=final_messages,
            model_name=model,
            error_message="聊天失败",
            config_overrides=kwargs,
            llm_tools=tools,
            tool_choice=tool_choice,
        )

        should_preserve = (
            preserve_media_in_history
            if preserve_media_in_history is not None
            else self.config.default_preserve_media_in_history
        )

        if should_preserve:
            logger.debug("深度分析模式：在历史记录中保留原始多模态消息。")
            self.history.append(current_message)
        else:
            logger.debug("高效模式：净化历史记录中的多模态消息。")
            sanitized_user_message = self._sanitize_message_for_history(current_message)
            self.history.append(sanitized_user_message)

        self.history.append(
            LLMMessage(
                role="assistant", content=response.text, tool_calls=response.tool_calls
            )
        )

        return response

    async def code(
        self,
        prompt: str,
        *,
        model: ModelName = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        代码执行

        参数:
            prompt: 代码执行的提示词。
            model: 要使用的模型名称。
            timeout: 代码执行超时时间（秒）。
            **kwargs: 传递给模型的其他参数。

        返回:
            dict[str, Any]: 包含执行结果的字典，包含text、code_executions和success字段。
        """
        resolved_model = model or self.config.model or "Gemini/gemini-2.0-flash"

        config = CommonOverrides.gemini_code_execution()
        if timeout:
            config.custom_params = config.custom_params or {}
            config.custom_params["code_execution_timeout"] = timeout

        messages = [LLMMessage.user(prompt)]

        response = await self._execute_generation(
            messages=messages,
            model_name=resolved_model,
            error_message="代码执行失败",
            config_overrides=kwargs,
            base_config=config,
        )

        return {
            "text": response.text,
            "code_executions": response.code_executions or [],
            "success": True,
        }

    async def search(
        self,
        query: str | UniMessage,
        *,
        model: ModelName = None,
        instruction: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        信息搜索 - 支持多模态输入

        参数:
            query: 搜索查询内容，支持文本或多模态消息。
            model: 要使用的模型名称。
            instruction: 搜索指令。
            **kwargs: 传递给模型的其他参数。

        返回:
            dict[str, Any]: 包含搜索结果的字典，包含text、sources、queries和success字段
        """
        from nonebot_plugin_alconna.uniseg import UniMessage

        resolved_model = model or self.config.model or "Gemini/gemini-2.0-flash"
        config = CommonOverrides.gemini_grounding()

        if isinstance(query, str):
            messages = [LLMMessage.user(query)]
        elif isinstance(query, UniMessage):
            content_parts = await unimsg_to_llm_parts(query)

            final_messages: list[LLMMessage] = []
            if instruction:
                final_messages.append(LLMMessage.system(instruction))

            if not content_parts:
                if instruction:
                    final_messages.append(LLMMessage.user(instruction))
                else:
                    raise LLMException(
                        "搜索内容为空或无法处理。", code=LLMErrorCode.API_REQUEST_FAILED
                    )
            else:
                final_messages.append(LLMMessage.user(content_parts))

            messages = final_messages
        else:
            raise LLMException(
                f"不支持的搜索输入类型: {type(query)}. 请使用 str 或 UniMessage.",
                code=LLMErrorCode.API_REQUEST_FAILED,
            )

        response = await self._execute_generation(
            messages=messages,
            model_name=resolved_model,
            error_message="信息搜索失败",
            config_overrides=kwargs,
            base_config=config,
        )

        result = {
            "text": response.text,
            "sources": [],
            "queries": [],
            "success": True,
        }

        if response.grounding_metadata:
            result["sources"] = response.grounding_metadata.grounding_attributions or []
            result["queries"] = response.grounding_metadata.web_search_queries or []

        return result

    async def analyze(
        self,
        message: UniMessage | None,
        *,
        instruction: str = "",
        model: ModelName = None,
        use_tools: list[str] | None = None,
        tool_config: dict[str, Any] | None = None,
        activated_tools: list[LLMTool] | None = None,
        history: list[LLMMessage] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        内容分析 - 接收 UniMessage 物件进行多模态分析和工具呼叫。

        参数:
            message: 要分析的消息内容（支持多模态）。
            instruction: 分析指令。
            model: 要使用的模型名称。
            use_tools: 要使用的工具名称列表。
            tool_config: 工具配置。
            activated_tools: 已激活的工具列表。
            history: 对话历史记录。
            **kwargs: 传递给模型的其他参数。

        返回:
            LLMResponse: 模型的完整响应结果。
        """
        from nonebot_plugin_alconna.uniseg import UniMessage

        content_parts = await unimsg_to_llm_parts(message or UniMessage())

        final_messages: list[LLMMessage] = []
        if history:
            final_messages.extend(history)

        if instruction:
            if not any(msg.role == "system" for msg in final_messages):
                final_messages.insert(0, LLMMessage.system(instruction))

        if not content_parts:
            if instruction and not history:
                final_messages.append(LLMMessage.user(instruction))
            elif not history:
                raise LLMException(
                    "分析内容为空或无法处理。", code=LLMErrorCode.API_REQUEST_FAILED
                )
        else:
            final_messages.append(LLMMessage.user(content_parts))

        llm_tools: list[LLMTool] | None = activated_tools
        if not llm_tools and use_tools:
            try:
                llm_tools = tool_registry.get_tools(use_tools)
                logger.debug(f"已从注册表加载工具定义: {use_tools}")
            except ValueError as e:
                raise LLMException(
                    f"加载工具定义失败: {e}",
                    code=LLMErrorCode.CONFIGURATION_ERROR,
                    cause=e,
                )

        tool_choice = None
        if tool_config:
            mode = tool_config.get("mode", "auto")
            if mode in ["auto", "any", "none"]:
                tool_choice = mode

        response = await self._execute_generation(
            messages=final_messages,
            model_name=model,
            error_message="内容分析失败",
            config_overrides=kwargs,
            llm_tools=llm_tools,
            tool_choice=tool_choice,
        )

        return response

    async def _execute_generation(
        self,
        messages: list[LLMMessage],
        model_name: ModelName,
        error_message: str,
        config_overrides: dict[str, Any],
        llm_tools: list[LLMTool] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        base_config: LLMGenerationConfig | None = None,
    ) -> LLMResponse:
        """通用的生成执行方法，封装模型获取和单次API调用"""
        try:
            resolved_model_name = self._resolve_model_name(
                model_name or self.config.model
            )
            final_config_dict = self._merge_config(
                config_overrides, base_config=base_config
            )

            async with await get_model_instance(
                resolved_model_name, override_config=final_config_dict
            ) as model_instance:
                return await model_instance.generate_response(
                    messages,
                    tools=llm_tools,
                    tool_choice=tool_choice,
                )
        except LLMException:
            raise
        except Exception as e:
            logger.error(f"{error_message}: {e}", e=e)
            raise LLMException(f"{error_message}: {e}", cause=e)

    def _resolve_model_name(self, model_name: ModelName) -> str:
        """解析模型名称"""
        if model_name:
            return model_name

        default_model = get_global_default_model_name()
        if default_model:
            return default_model

        raise LLMException(
            "未指定模型名称且未设置全局默认模型",
            code=LLMErrorCode.MODEL_NOT_FOUND,
        )

    def _merge_config(
        self,
        user_config: dict[str, Any],
        base_config: LLMGenerationConfig | None = None,
    ) -> dict[str, Any]:
        """合并配置"""
        final_config = {}
        if base_config:
            final_config.update(base_config.to_dict())

        if self.config.temperature is not None:
            final_config["temperature"] = self.config.temperature
        if self.config.max_tokens is not None:
            final_config["max_tokens"] = self.config.max_tokens

        if self.config.enable_cache:
            final_config["enable_caching"] = True
        if self.config.enable_code:
            final_config["enable_code_execution"] = True
        if self.config.enable_search:
            final_config["enable_grounding"] = True

        if self.config.enable_gemini_json_mode:
            final_config["response_mime_type"] = "application/json"
        if self.config.enable_gemini_thinking:
            final_config["thinking_budget"] = 0.8
        if self.config.enable_gemini_safe_mode:
            final_config["safety_settings"] = (
                CommonOverrides.gemini_safe().safety_settings
            )
        if self.config.enable_gemini_multimodal:
            final_config.update(CommonOverrides.gemini_multimodal().to_dict())
        if self.config.enable_gemini_grounding:
            final_config["enable_grounding"] = True

        final_config.update(user_config)

        return final_config

    async def embed(
        self,
        texts: list[str] | str,
        *,
        model: ModelName = None,
        task_type: EmbeddingTaskType | str = EmbeddingTaskType.RETRIEVAL_DOCUMENT,
        **kwargs: Any,
    ) -> list[list[float]]:
        """
        生成文本嵌入向量

        参数:
            texts: 要生成嵌入向量的文本或文本列表。
            model: 要使用的嵌入模型名称。
            task_type: 嵌入任务类型。
            **kwargs: 传递给模型的其他参数。

        返回:
            list[list[float]]: 文本的嵌入向量列表。
        """
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return []

        try:
            resolved_model_str = (
                model or self.config.default_embedding_model or self.config.model
            )
            if not resolved_model_str:
                raise LLMException(
                    "使用 embed 功能时必须指定嵌入模型名称，"
                    "或在 AIConfig 中配置 default_embedding_model。",
                    code=LLMErrorCode.MODEL_NOT_FOUND,
                )
            resolved_model_str = self._resolve_model_name(resolved_model_str)

            async with await get_model_instance(
                resolved_model_str,
                override_config=None,
            ) as embedding_model_instance:
                return await embedding_model_instance.generate_embeddings(
                    texts, task_type=task_type, **kwargs
                )
        except LLMException:
            raise
        except Exception as e:
            logger.error(f"文本嵌入失败: {e}", e=e)
            raise LLMException(
                f"文本嵌入失败: {e}", code=LLMErrorCode.EMBEDDING_FAILED, cause=e
            )
