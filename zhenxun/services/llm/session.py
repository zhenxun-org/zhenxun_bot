"""
LLM 服务 - 会话客户端

提供一个有状态的、面向会话的 LLM 客户端，用于进行多轮对话和复杂交互。
"""

import copy
from dataclasses import dataclass, field
import json
from typing import Any, TypeVar
import uuid

from jinja2 import Environment
from nonebot.compat import type_validate_json
from nonebot_plugin_alconna.uniseg import UniMessage
from pydantic import BaseModel, ValidationError

from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_copy, model_dump, model_json_schema

from .config import (
    CommonOverrides,
    LLMGenerationConfig,
)
from .config.providers import get_ai_config
from .manager import get_global_default_model_name, get_model_instance
from .memory import BaseMemory, InMemoryMemory
from .tools.manager import tool_provider_manager
from .types import (
    EmbeddingTaskType,
    LLMContentPart,
    LLMErrorCode,
    LLMException,
    LLMMessage,
    LLMResponse,
    ModelName,
    ResponseFormat,
    ToolExecutable,
    ToolProvider,
)
from .utils import normalize_to_llm_messages

T = TypeVar("T", bound=BaseModel)

jinja_env = Environment(autoescape=False)


@dataclass
class AIConfig:
    """AI配置类 - [重构后] 简化版本"""

    model: ModelName = None
    default_embedding_model: ModelName = None
    default_preserve_media_in_history: bool = False
    tool_providers: list[ToolProvider] = field(default_factory=list)

    def __post_init__(self):
        """初始化后从配置中读取默认值"""
        ai_config = get_ai_config()
        if self.model is None:
            self.model = ai_config.get("default_model_name")


class AI:
    """
    统一的AI服务类 - 提供了带记忆的会话接口。
    不再执行自主工具循环，当LLM返回工具调用时，会直接将请求返回给调用者。
    """

    def __init__(
        self,
        session_id: str | None = None,
        config: AIConfig | None = None,
        memory: BaseMemory | None = None,
        default_generation_config: LLMGenerationConfig | None = None,
    ):
        """
        初始化AI服务

        参数:
            session_id: 唯一的会话ID，用于隔离记忆。
            config: AI 配置.
            memory: 可选的自定义记忆后端。如果为None，则使用默认的InMemoryMemory。
            default_generation_config: (新增) 此AI实例的默认生成配置。
        """
        self.session_id = session_id or str(uuid.uuid4())
        self.config = config or AIConfig()
        self.memory = memory or InMemoryMemory()
        self.default_generation_config = (
            default_generation_config or LLMGenerationConfig()
        )

        global_providers = tool_provider_manager._providers
        config_providers = self.config.tool_providers
        self._tool_providers = list(dict.fromkeys(global_providers + config_providers))

    async def clear_history(self):
        """清空当前会话的历史记录。"""
        await self.memory.clear_history(self.session_id)
        logger.info(f"AI会话历史记录已清空 (session_id: {self.session_id})")

    async def add_user_message_to_history(
        self, message: str | LLMMessage | list[LLMContentPart]
    ):
        """
        将一条用户消息标准化并添加到会话历史中。

        参数:
            message: 用户消息内容。
        """
        user_message = await self._normalize_input_to_message(message)
        await self.memory.add_message(self.session_id, user_message)

    async def add_assistant_response_to_history(self, response_text: str):
        """
        将助手的文本回复添加到会话历史中。

        参数:
            response_text: 助手的回复文本。
        """
        assistant_message = LLMMessage.assistant_text_response(response_text)
        await self.memory.add_message(self.session_id, assistant_message)

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

    async def _normalize_input_to_message(
        self, message: str | UniMessage | LLMMessage | list[LLMContentPart]
    ) -> LLMMessage:
        """
        [重构后] 内部辅助方法，将各种输入类型统一转换为单个 LLMMessage 对象。
        它调用共享的工具函数并提取最后一条消息（通常是用户输入）。
        """
        messages = await normalize_to_llm_messages(message)

        if not messages:
            raise LLMException(
                "无法将输入标准化为有效的消息。", code=LLMErrorCode.CONFIGURATION_ERROR
            )
        return messages[-1]

    async def chat(
        self,
        message: str | UniMessage | LLMMessage | list[LLMContentPart],
        *,
        model: ModelName = None,
        instruction: str | None = None,
        template_vars: dict[str, Any] | None = None,
        preserve_media_in_history: bool | None = None,
        tools: list[dict[str, Any] | str] | dict[str, ToolExecutable] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        config: LLMGenerationConfig | None = None,
    ) -> LLMResponse:
        """
        核心交互方法，管理会话历史并执行单次LLM调用。

        参数:
            message: 用户输入的消息内容，支持文本、UniMessage、LLMMessage或
                    内容部分列表。
            model: 要使用的模型名称，如果为None则使用配置中的默认模型。
            instruction: 本次调用的特定系统指令，会与全局指令合并。
            template_vars: 模板变量字典，用于在指令中进行变量替换。
            preserve_media_in_history: 是否在历史记录中保留媒体内容，
                                     None时使用默认配置。
            tools: 可用的工具列表或工具字典，支持临时工具和预配置工具。
            tool_choice: 工具选择策略，控制AI如何选择和使用工具。
            config: 生成配置对象，用于覆盖默认的生成参数。

        返回:
            LLMResponse: 包含AI回复、工具调用请求、使用信息等的完整响应对象。
        """
        current_message = await self._normalize_input_to_message(message)

        messages_for_run = []
        final_instruction = instruction

        if final_instruction and template_vars:
            try:
                template = jinja_env.from_string(final_instruction)
                final_instruction = template.render(**template_vars)
                logger.debug(f"渲染后的系统指令: {final_instruction}")
            except Exception as e:
                logger.error(f"渲染系统指令模板失败: {e}", e=e)

        if final_instruction:
            messages_for_run.append(LLMMessage.system(final_instruction))

        current_history = await self.memory.get_history(self.session_id)
        messages_for_run.extend(current_history)
        messages_for_run.append(current_message)

        try:
            resolved_model_name = self._resolve_model_name(model or self.config.model)

            final_config = model_copy(self.default_generation_config, deep=True)
            if config:
                update_dict = model_dump(config, exclude_unset=True)
                final_config = model_copy(final_config, update=update_dict)

            ad_hoc_tools = None
            if tools:
                if isinstance(tools, dict):
                    ad_hoc_tools = tools
                else:
                    ad_hoc_tools = await self._resolve_tools(tools)

            async with await get_model_instance(
                resolved_model_name,
                override_config=final_config.to_dict(),
            ) as model_instance:
                response = await model_instance.generate_response(
                    messages_for_run, tools=ad_hoc_tools, tool_choice=tool_choice
                )

            should_preserve = (
                preserve_media_in_history
                if preserve_media_in_history is not None
                else self.config.default_preserve_media_in_history
            )
            user_msg_to_store = (
                current_message
                if should_preserve
                else self._sanitize_message_for_history(current_message)
            )
            assistant_response_msg = LLMMessage.assistant_text_response(response.text)
            if response.tool_calls:
                assistant_response_msg = LLMMessage.assistant_tool_calls(
                    response.tool_calls, response.text
                )

            await self.memory.add_messages(
                self.session_id, [user_msg_to_store, assistant_response_msg]
            )

            return response

        except Exception as e:
            raise (
                e
                if isinstance(e, LLMException)
                else LLMException(f"聊天执行失败: {e}", cause=e)
            )

    async def code(
        self,
        prompt: str,
        *,
        model: ModelName = None,
        timeout: int | None = None,
        config: LLMGenerationConfig | None = None,
    ) -> LLMResponse:
        """
        代码执行

        参数:
            prompt: 代码执行的提示词。
            model: 要使用的模型名称。
            timeout: 代码执行超时时间（秒）。
            config: (可选) 覆盖默认的生成配置。

        返回:
            LLMResponse: 包含执行结果的完整响应对象。
        """
        resolved_model = model or self.config.model or "Gemini/gemini-2.0-flash"

        code_config = CommonOverrides.gemini_code_execution()
        if timeout:
            code_config.custom_params = code_config.custom_params or {}
            code_config.custom_params["code_execution_timeout"] = timeout

        if config:
            update_dict = model_dump(config, exclude_unset=True)
            code_config = model_copy(code_config, update=update_dict)

        return await self.chat(prompt, model=resolved_model, config=code_config)

    async def search(
        self,
        query: UniMessage,
        *,
        model: ModelName = None,
        instruction: str = (
            "你是一位强大的信息检索和整合专家。请利用可用的搜索工具，"
            "根据用户的查询找到最相关的信息，并进行总结和回答。"
        ),
        template_vars: dict[str, Any] | None = None,
        config: LLMGenerationConfig | None = None,
    ) -> LLMResponse:
        """
        信息搜索的便捷入口，原生支持多模态查询。
        """
        logger.info("执行 'search' 任务...")
        search_config = CommonOverrides.gemini_grounding()

        if config:
            update_dict = model_dump(config, exclude_unset=True)
            search_config = model_copy(search_config, update=update_dict)

        return await self.chat(
            query,
            model=model,
            instruction=instruction,
            template_vars=template_vars,
            config=search_config,
        )

    async def generate_structured(
        self,
        message: str | LLMMessage | list[LLMContentPart],
        response_model: type[T],
        *,
        model: ModelName = None,
        instruction: str | None = None,
        config: LLMGenerationConfig | None = None,
    ) -> T:
        """
        生成结构化响应，并自动解析为指定的Pydantic模型。

        参数:
            message: 用户输入的消息内容，支持多种格式。
            response_model: 用于解析和验证响应的Pydantic模型类。
            model: 要使用的模型名称，如果为None则使用配置中的默认模型。
            instruction: 本次调用的特定系统指令，会与JSON Schema指令合并。
            config: 生成配置对象，用于覆盖默认的生成参数。

        返回:
            T: 解析后的Pydantic模型实例，类型为response_model指定的类型。

        异常:
            LLMException: 如果模型返回的不是有效的JSON或验证失败。
        """
        try:
            json_schema = model_json_schema(response_model)
        except AttributeError:
            json_schema = response_model.schema()

        schema_str = json.dumps(json_schema, ensure_ascii=False, indent=2)

        system_prompt = (
            (f"{instruction}\n\n" if instruction else "")
            + "你必须严格按照以下 JSON Schema 格式进行响应。"
            + "不要包含任何额外的解释、注释或代码块标记，只返回纯粹的 JSON 对象。\n\n"
        )
        system_prompt += f"JSON Schema:\n```json\n{schema_str}\n```"

        final_config = model_copy(config) if config else LLMGenerationConfig()

        final_config.response_format = ResponseFormat.JSON
        final_config.response_schema = json_schema

        response = await self.chat(
            message, model=model, instruction=system_prompt, config=final_config
        )

        try:
            return type_validate_json(response_model, response.text)
        except ValidationError as e:
            logger.error(f"LLM结构化输出验证失败: {e}", e=e)
            raise LLMException(
                "LLM返回的JSON未能通过结构验证。",
                code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                details={"raw_response": response.text, "validation_error": str(e)},
                cause=e,
            )
        except Exception as e:
            logger.error(f"解析LLM结构化输出时发生未知错误: {e}", e=e)
            raise LLMException(
                "解析LLM的JSON输出时失败。",
                code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                details={"raw_response": response.text},
                cause=e,
            )

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

    async def embed(
        self,
        texts: list[str] | str,
        *,
        model: ModelName = None,
        task_type: EmbeddingTaskType | str = EmbeddingTaskType.RETRIEVAL_DOCUMENT,
        **kwargs: Any,
    ) -> list[list[float]]:
        """
        生成文本嵌入向量，将文本转换为数值向量表示。

        参数:
            texts: 要生成嵌入的文本内容，支持单个字符串或字符串列表。
            model: 嵌入模型名称，如果为None则使用配置中的默认嵌入模型。
            task_type: 嵌入任务类型，影响向量的优化方向（如检索、分类等）。
            **kwargs: 传递给嵌入模型的额外参数。

        返回:
            list[list[float]]: 文本对应的嵌入向量列表，每个向量为浮点数列表。

        异常:
            LLMException: 如果嵌入生成失败或模型配置错误。
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

    async def _resolve_tools(
        self,
        tool_configs: list[Any],
    ) -> dict[str, ToolExecutable]:
        """
        使用注入的 ToolProvider 异步解析 ad-hoc（临时）工具配置。
        返回一个从工具名称到可执行对象的字典。
        """
        resolved: dict[str, ToolExecutable] = {}

        for config in tool_configs:
            name = config if isinstance(config, str) else config.get("name")
            if not name:
                raise LLMException(
                    "工具配置字典必须包含 'name' 字段。",
                    code=LLMErrorCode.CONFIGURATION_ERROR,
                )

            if isinstance(config, str):
                config_dict = {"name": name, "type": "function"}
            elif isinstance(config, dict):
                config_dict = config
            else:
                raise TypeError(f"不支持的工具配置类型: {type(config)}")

            executable = None
            for provider in self._tool_providers:
                executable = await provider.get_tool_executable(name, config_dict)
                if executable:
                    break

            if not executable:
                raise LLMException(
                    f"没有为 ad-hoc 工具 '{name}' 找到合适的提供者。",
                    code=LLMErrorCode.CONFIGURATION_ERROR,
                )

            resolved[name] = executable

        return resolved
