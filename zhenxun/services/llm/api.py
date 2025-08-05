"""
LLM 服务的高级 API 接口 - 便捷函数入口 (无状态)
"""

from typing import Any, TypeVar

from nonebot_plugin_alconna.uniseg import UniMessage
from pydantic import BaseModel

from zhenxun.services.log import logger

from .config import CommonOverrides
from .config.generation import create_generation_config_from_kwargs
from .manager import get_model_instance
from .session import AI
from .tools.manager import tool_provider_manager
from .types import (
    EmbeddingTaskType,
    LLMContentPart,
    LLMErrorCode,
    LLMException,
    LLMMessage,
    LLMResponse,
    ModelName,
)

T = TypeVar("T", bound=BaseModel)


async def chat(
    message: str | UniMessage | LLMMessage | list[LLMContentPart],
    *,
    model: ModelName = None,
    instruction: str | None = None,
    tools: list[dict[str, Any] | str] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """
    无状态的聊天对话便捷函数，通过临时的AI会话实例与LLM模型交互。

    参数:
        message: 用户输入的消息内容，支持多种格式。
        model: 要使用的模型名称，如果为None则使用默认模型。
        instruction: 系统指令，用于指导AI的行为和回复风格。
        tools: 可用的工具列表，支持字典配置或字符串标识符。
        tool_choice: 工具选择策略，控制AI如何选择和使用工具。
        **kwargs: 额外的生成配置参数，会被转换为LLMGenerationConfig。

    返回:
        LLMResponse: 包含AI回复内容、使用信息和工具调用等的完整响应对象。
    """
    try:
        config = create_generation_config_from_kwargs(**kwargs) if kwargs else None

        ai_session = AI()

        return await ai_session.chat(
            message,
            model=model,
            instruction=instruction,
            tools=tools,
            tool_choice=tool_choice,
            config=config,
        )
    except LLMException:
        raise
    except Exception as e:
        logger.error(f"执行 chat 函数失败: {e}", e=e)
        raise LLMException(f"聊天执行失败: {e}", cause=e)


async def code(
    prompt: str,
    *,
    model: ModelName = None,
    timeout: int | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """
    无状态的代码执行便捷函数，支持在沙箱环境中执行代码。

    参数:
        prompt: 代码执行的提示词，描述要执行的代码任务。
        model: 要使用的模型名称，默认使用Gemini/gemini-2.0-flash。
        timeout: 代码执行超时时间（秒），防止长时间运行的代码阻塞。
        **kwargs: 额外的生成配置参数。

    返回:
        LLMResponse: 包含代码执行结果的完整响应对象。
    """
    resolved_model = model or "Gemini/gemini-2.0-flash"

    config = CommonOverrides.gemini_code_execution()
    if timeout:
        config.custom_params = config.custom_params or {}
        config.custom_params["code_execution_timeout"] = timeout

    final_config = config.to_dict()
    final_config.update(kwargs)

    return await chat(prompt, model=resolved_model, **final_config)


async def search(
    query: str | UniMessage | LLMMessage | list[LLMContentPart],
    *,
    model: ModelName = None,
    instruction: str = (
        "你是一位强大的信息检索和整合专家。请利用可用的搜索工具，"
        "根据用户的查询找到最相关的信息，并进行总结和回答。"
    ),
    **kwargs: Any,
) -> LLMResponse:
    """
    无状态的信息搜索便捷函数，利用搜索工具获取实时信息。

    参数:
        query: 搜索查询内容，支持多种输入格式。
        model: 要使用的模型名称，如果为None则使用默认模型。
        instruction: 搜索任务的系统指令，指导AI如何处理搜索结果。
        **kwargs: 额外的生成配置参数。

    返回:
        LLMResponse: 包含搜索结果和AI整合回复的完整响应对象。
    """
    logger.debug("执行无状态 'search' 任务...")
    search_config = CommonOverrides.gemini_grounding()

    final_config = search_config.to_dict()
    final_config.update(kwargs)

    return await chat(
        query,
        model=model,
        instruction=instruction,
        **final_config,
    )


async def embed(
    texts: list[str] | str,
    *,
    model: ModelName = None,
    task_type: EmbeddingTaskType | str = EmbeddingTaskType.RETRIEVAL_DOCUMENT,
    **kwargs: Any,
) -> list[list[float]]:
    """
    无状态的文本嵌入便捷函数，将文本转换为向量表示。

    参数:
        texts: 要生成嵌入的文本内容，支持单个字符串或字符串列表。
        model: 要使用的嵌入模型名称，如果为None则使用默认模型。
        task_type: 嵌入任务类型，影响向量的优化方向（如检索、分类等）。
        **kwargs: 额外的模型配置参数。

    返回:
        list[list[float]]: 文本对应的嵌入向量列表，每个向量为浮点数列表。
    """
    if isinstance(texts, str):
        texts = [texts]
    if not texts:
        return []

    try:
        async with await get_model_instance(model) as model_instance:
            return await model_instance.generate_embeddings(
                texts, task_type=task_type, **kwargs
            )
    except LLMException:
        raise
    except Exception as e:
        logger.error(f"文本嵌入失败: {e}", e=e)
        raise LLMException(
            f"文本嵌入失败: {e}", code=LLMErrorCode.EMBEDDING_FAILED, cause=e
        )


async def generate_structured(
    message: str | LLMMessage | list[LLMContentPart],
    response_model: type[T],
    *,
    model: ModelName = None,
    instruction: str | None = None,
    **kwargs: Any,
) -> T:
    """
    无状态地生成结构化响应，并自动解析为指定的Pydantic模型。

    参数:
        message: 用户输入的消息内容，支持多种格式。
        response_model: 用于解析和验证响应的Pydantic模型类。
        model: 要使用的模型名称，如果为None则使用默认模型。
        instruction: 系统指令，用于指导AI生成符合要求的结构化输出。
        **kwargs: 额外的生成配置参数。

    返回:
        T: 解析后的Pydantic模型实例，类型为response_model指定的类型。
    """
    try:
        config = create_generation_config_from_kwargs(**kwargs) if kwargs else None

        ai_session = AI()

        return await ai_session.generate_structured(
            message,
            response_model,
            model=model,
            instruction=instruction,
            config=config,
        )
    except LLMException:
        raise
    except Exception as e:
        logger.error(f"生成结构化响应失败: {e}", e=e)
        raise LLMException(f"生成结构化响应失败: {e}", cause=e)


async def generate(
    messages: list[LLMMessage],
    *,
    model: ModelName = None,
    tools: list[dict[str, Any] | str] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """
    根据完整的消息列表生成一次性响应，这是一个无状态的底层函数。

    参数:
        messages: 完整的消息历史列表，包括系统指令、用户消息和助手回复。
        model: 要使用的模型名称，如果为None则使用默认模型。
        tools: 可用的工具列表，支持字典配置或字符串标识符。
        tool_choice: 工具选择策略，控制AI如何选择和使用工具。
        **kwargs: 额外的生成配置参数，会覆盖默认配置。

    返回:
        LLMResponse: 包含AI回复内容、使用信息和工具调用等的完整响应对象。
    """
    try:
        async with await get_model_instance(
            model, override_config=kwargs
        ) as model_instance:
            return await model_instance.generate_response(
                messages,
                tools=tools,  # type: ignore
                tool_choice=tool_choice,
            )
    except LLMException:
        raise
    except Exception as e:
        logger.error(f"生成响应失败: {e}", e=e)
        raise LLMException(f"生成响应失败: {e}", cause=e)


async def run_with_tools(
    message: str | UniMessage | LLMMessage | list[LLMContentPart],
    *,
    model: ModelName = None,
    instruction: str | None = None,
    tools: list[str],
    max_cycles: int = 5,
    **kwargs: Any,
) -> LLMResponse:
    """
    无状态地执行一个带本地Python函数的LLM调用循环。

    参数:
        message: 用户输入。
        model: 使用的模型。
        instruction: 系统指令。
        tools: 要使用的本地函数工具名称列表 (必须已通过 @function_tool 注册)。
        max_cycles: 最大工具调用循环次数。
        **kwargs: 额外的生成配置参数。

    返回:
        LLMResponse: 包含最终回复的响应对象。
    """
    from .executor import ExecutionConfig, LLMToolExecutor
    from .utils import normalize_to_llm_messages

    messages = await normalize_to_llm_messages(message, instruction)

    async with await get_model_instance(
        model, override_config=kwargs
    ) as model_instance:
        resolved_tools = await tool_provider_manager.get_function_tools(tools)
        if not resolved_tools:
            logger.warning(
                "run_with_tools 未找到任何可用的本地函数工具，将作为普通聊天执行。"
            )
            return await model_instance.generate_response(messages, tools=None)

        executor = LLMToolExecutor(model_instance)
        config = ExecutionConfig(max_cycles=max_cycles)
        final_history = await executor.run(messages, resolved_tools, config)

        for msg in reversed(final_history):
            if msg.role == "assistant":
                text = msg.content if isinstance(msg.content, str) else str(msg.content)
                return LLMResponse(text=text, tool_calls=msg.tool_calls)

    raise LLMException(
        "带工具的执行循环未能产生有效的助手回复。", code=LLMErrorCode.GENERATION_FAILED
    )
