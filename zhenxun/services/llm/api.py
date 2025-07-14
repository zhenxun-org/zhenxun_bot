"""
LLM 服务的高级 API 接口 - 便捷函数入口
"""

from pathlib import Path
from typing import Any

from nonebot_plugin_alconna.uniseg import UniMessage

from zhenxun.services.log import logger

from .manager import get_model_instance
from .session import AI
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
from .utils import create_multimodal_message, unimsg_to_llm_parts


async def chat(
    message: str | LLMMessage | list[LLMContentPart],
    *,
    model: ModelName = None,
    tools: list[LLMTool] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """
    聊天对话便捷函数

    参数:
        message: 用户输入的消息。
        model: 要使用的模型名称。
        tools: 本次对话可用的工具列表。
        tool_choice: 强制模型使用的工具。
        **kwargs: 传递给模型的其他参数。

    返回:
        LLMResponse: 模型的完整响应，可能包含文本或工具调用请求。
    """
    ai = AI()
    return await ai.chat(
        message, model=model, tools=tools, tool_choice=tool_choice, **kwargs
    )


async def code(
    prompt: str,
    *,
    model: ModelName = None,
    timeout: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    代码执行便捷函数

    参数:
        prompt: 代码执行的提示词。
        model: 要使用的模型名称。
        timeout: 代码执行超时时间（秒）。
        **kwargs: 传递给模型的其他参数。

    返回:
        dict[str, Any]: 包含执行结果的字典。
    """
    ai = AI()
    return await ai.code(prompt, model=model, timeout=timeout, **kwargs)


async def search(
    query: str | UniMessage,
    *,
    model: ModelName = None,
    instruction: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """
    信息搜索便捷函数

    参数:
        query: 搜索查询内容。
        model: 要使用的模型名称。
        instruction: 搜索指令。
        **kwargs: 传递给模型的其他参数。

    返回:
        dict[str, Any]: 包含搜索结果的字典。
    """
    ai = AI()
    return await ai.search(query, model=model, instruction=instruction, **kwargs)


async def analyze(
    message: UniMessage | None,
    *,
    instruction: str = "",
    model: ModelName = None,
    use_tools: list[str] | None = None,
    tool_config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> str | LLMResponse:
    """
    内容分析便捷函数

    参数:
        message: 要分析的消息内容。
        instruction: 分析指令。
        model: 要使用的模型名称。
        use_tools: 要使用的工具名称列表。
        tool_config: 工具配置。
        **kwargs: 传递给模型的其他参数。

    返回:
        str | LLMResponse: 分析结果。
    """
    ai = AI()
    return await ai.analyze(
        message,
        instruction=instruction,
        model=model,
        use_tools=use_tools,
        tool_config=tool_config,
        **kwargs,
    )


async def analyze_multimodal(
    text: str | None = None,
    images: list[str | Path | bytes] | str | Path | bytes | None = None,
    videos: list[str | Path | bytes] | str | Path | bytes | None = None,
    audios: list[str | Path | bytes] | str | Path | bytes | None = None,
    *,
    instruction: str = "",
    model: ModelName = None,
    **kwargs: Any,
) -> str | LLMResponse:
    """
    多模态分析便捷函数

    参数:
        text: 文本内容。
        images: 图片文件路径、字节数据或列表。
        videos: 视频文件路径、字节数据或列表。
        audios: 音频文件路径、字节数据或列表。
        instruction: 分析指令。
        model: 要使用的模型名称。
        **kwargs: 传递给模型的其他参数。

    返回:
        str | LLMResponse: 分析结果。
    """
    message = create_multimodal_message(
        text=text, images=images, videos=videos, audios=audios
    )
    return await analyze(message, instruction=instruction, model=model, **kwargs)


async def search_multimodal(
    text: str | None = None,
    images: list[str | Path | bytes] | str | Path | bytes | None = None,
    videos: list[str | Path | bytes] | str | Path | bytes | None = None,
    audios: list[str | Path | bytes] | str | Path | bytes | None = None,
    *,
    instruction: str = "",
    model: ModelName = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    多模态搜索便捷函数

    参数:
        text: 文本内容。
        images: 图片文件路径、字节数据或列表。
        videos: 视频文件路径、字节数据或列表。
        audios: 音频文件路径、字节数据或列表。
        instruction: 搜索指令。
        model: 要使用的模型名称。
        **kwargs: 传递给模型的其他参数。

    返回:
        dict[str, Any]: 包含搜索结果的字典。
    """
    message = create_multimodal_message(
        text=text, images=images, videos=videos, audios=audios
    )
    ai = AI()
    return await ai.search(message, model=model, instruction=instruction, **kwargs)


async def embed(
    texts: list[str] | str,
    *,
    model: ModelName = None,
    task_type: EmbeddingTaskType | str = EmbeddingTaskType.RETRIEVAL_DOCUMENT,
    **kwargs: Any,
) -> list[list[float]]:
    """
    文本嵌入便捷函数

    参数:
        texts: 要生成嵌入向量的文本或文本列表。
        model: 要使用的嵌入模型名称。
        task_type: 嵌入任务类型。
        **kwargs: 传递给模型的其他参数。

    返回:
        list[list[float]]: 文本的嵌入向量列表。
    """
    ai = AI()
    return await ai.embed(texts, model=model, task_type=task_type, **kwargs)


async def pipeline_chat(
    message: UniMessage | str | list[LLMContentPart],
    model_chain: list[ModelName],
    *,
    initial_instruction: str = "",
    final_instruction: str = "",
    **kwargs: Any,
) -> LLMResponse:
    """
    AI模型链式调用，前一个模型的输出作为下一个模型的输入。

    参数:
        message: 初始输入消息（支持多模态）
        model_chain: 模型名称列表
        initial_instruction: 第一个模型的系统指令
        final_instruction: 最后一个模型的系统指令
        **kwargs: 传递给模型实例的其他参数

    返回:
        LLMResponse: 最后一个模型的响应结果
    """
    if not model_chain:
        raise ValueError("模型链`model_chain`不能为空。")

    current_content: str | list[LLMContentPart]
    if isinstance(message, UniMessage):
        current_content = await unimsg_to_llm_parts(message)
    elif isinstance(message, str):
        current_content = message
    elif isinstance(message, list):
        current_content = message
    else:
        raise TypeError(f"不支持的消息类型: {type(message)}")

    final_response: LLMResponse | None = None

    for i, model_name in enumerate(model_chain):
        if not model_name:
            raise ValueError(f"模型链中第 {i + 1} 个模型名称为空。")

        is_first_step = i == 0
        is_last_step = i == len(model_chain) - 1

        messages_for_step: list[LLMMessage] = []
        instruction_for_step = ""
        if is_first_step and initial_instruction:
            instruction_for_step = initial_instruction
        elif is_last_step and final_instruction:
            instruction_for_step = final_instruction

        if instruction_for_step:
            messages_for_step.append(LLMMessage.system(instruction_for_step))

        messages_for_step.append(LLMMessage.user(current_content))

        logger.info(
            f"Pipeline Step [{i + 1}/{len(model_chain)}]: "
            f"使用模型 '{model_name}' 进行处理..."
        )
        try:
            async with await get_model_instance(model_name, **kwargs) as model:
                response = await model.generate_response(messages_for_step)
            final_response = response
            current_content = response.text.strip()
            if not current_content and not is_last_step:
                logger.warning(
                    f"模型 '{model_name}' 在中间步骤返回了空内容，流水线可能无法继续。"
                )
                break

        except Exception as e:
            logger.error(f"在模型链的第 {i + 1} 步 ('{model_name}') 出错: {e}", e=e)
            raise LLMException(
                f"流水线在模型 '{model_name}' 处执行失败: {e}",
                code=LLMErrorCode.GENERATION_FAILED,
                cause=e,
            )

    if final_response is None:
        raise LLMException(
            "AI流水线未能产生任何响应。", code=LLMErrorCode.GENERATION_FAILED
        )

    return final_response


async def generate(
    messages: list[LLMMessage],
    *,
    model: ModelName = None,
    tools: list[LLMTool] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """
    根据完整的消息列表（包括系统指令）生成一次性响应。
    这是一个便捷的函数，不使用或修改任何会话历史。

    参数:
        messages: 用于生成响应的完整消息列表。
        model: 要使用的模型名称。
        tools: 可用的工具列表。
        tool_choice: 工具选择策略。
        **kwargs: 传递给模型的其他参数。

    返回:
        LLMResponse: 模型的完整响应对象。
    """
    try:
        ai_instance = AI()
        resolved_model_name = ai_instance._resolve_model_name(model)
        final_config_dict = ai_instance._merge_config(kwargs)

        async with await get_model_instance(
            resolved_model_name, override_config=final_config_dict
        ) as model_instance:
            return await model_instance.generate_response(
                messages,
                tools=tools,
                tool_choice=tool_choice,
            )
    except LLMException:
        raise
    except Exception as e:
        logger.error(f"生成响应失败: {e}", e=e)
        raise LLMException(f"生成响应失败: {e}", cause=e)
