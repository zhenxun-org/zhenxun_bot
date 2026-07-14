"""
LLM 服务的高级 API 接口 - 便捷函数入口 (无状态)
"""

import json
from pathlib import Path
from typing import Any, Literal, TypeVar, overload

from pydantic import BaseModel

from zhenxun.services.ai.config import get_llm_config
from zhenxun.services.ai.core.exceptions import (
    ControlFlowExit,
    LLMException,
    ModelRetry,
    UpstreamServerException,
    get_user_friendly_error_message,
)
from zhenxun.services.ai.core.messages import (
    AudioResponse,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ImageRequest,
    ImageResponse,
    LLMMessage,
    PromptInput,
    RerankRequest,
    RerankResult,
    SpeechRequest,
    UsageInfo,
)
from zhenxun.services.ai.core.models import ModelName
from zhenxun.services.ai.core.options import (
    GenerationConfig,
    LLMEmbeddingConfig,
    OutputFormatConfig,
    ResponseFormat,
    StructuredOutputStrategy,
    TTSConfig,
)
from zhenxun.services.ai.guardrails import GuardrailSource, parse_guardrails
from zhenxun.services.ai.utils.logger import log_llm as logger

from .builder import IntentBuilder
from .engine.router import LLMOrchestrator

T = TypeVar("T", bound=BaseModel)


async def chat(
    message: PromptInput | list[LLMMessage],
    *,
    model: ModelName = None,
    instruction: str | None = None,
    config: GenerationConfig | IntentBuilder | None = None,
    timeout: float | None = None,
) -> ChatResponse:
    """
    无状态的聊天对话便捷函数，单次执行后立即销毁上下文。

    示例:
        response = await chat("你好", model="OpenAI/gpt-4o", instruction="你是一个助手")
        print(response.text)

    参数:
        message: 用户输入的消息内容，支持多种格式。
        model: 要使用的模型名称，如果为None则使用默认模型。
        instruction: 系统指令，用于指导AI的行为和回复风格。
        config: (可选) 配置构建器 IntentBuilder 或 GenerationConfig 对象。
        timeout: (可选) HTTP 请求超时时间（秒）。

    返回:
        ChatResponse: 包含AI回复内容、使用信息和工具调用等的完整响应对象。

    异常:
        LLMException: 当网络超时、模型不存在或 API 返回错误时抛出，建议外层捕获。
    """
    try:
        from zhenxun.services.ai.message_builder import MessageBuilder

        messages = await MessageBuilder.normalize_to_llm_messages(
            message, instruction=instruction
        )
        return await generate(
            messages=messages,
            model=model,
            config=config,
            timeout=timeout,
        )
    except LLMException as e:
        raise e.with_traceback(None) from None
    except Exception as e:
        friendly_msg = get_user_friendly_error_message(e)
        logger.error(f"执行 chat 函数失败: {e} | 建议: {friendly_msg}", e=e)
        raise LLMException(f"聊天执行失败: {friendly_msg}").with_traceback(
            None
        ) from None


@overload
async def embed(
    input_batch: PromptInput,
    *,
    model: ModelName = None,
    task: Literal[
        "general", "query", "document", "similarity", "classification", "clustering"
    ] = "general",
    dimensions: int | None = None,
    multimodal: bool | list[str] = False,
    config: LLMEmbeddingConfig | None = None,
) -> EmbeddingResponse: ...


@overload
async def embed(
    input_batch: list[PromptInput],
    *,
    model: ModelName = None,
    task: Literal[
        "general", "query", "document", "similarity", "classification", "clustering"
    ] = "general",
    dimensions: int | None = None,
    multimodal: bool | list[str] = False,
    config: LLMEmbeddingConfig | None = None,
) -> EmbeddingResponse: ...


async def embed(
    input_batch: PromptInput | list[PromptInput],
    *,
    model: ModelName = None,
    task: Literal[
        "general", "query", "document", "similarity", "classification", "clustering"
    ] = "general",
    dimensions: int | None = None,
    multimodal: bool | list[str] = False,
    config: LLMEmbeddingConfig | None = None,
) -> EmbeddingResponse:
    """
    无状态的向量嵌入便捷函数，支持文本批量与图文多模态融合 (Fused Embeddings)。

    参数:
        input_batch: 要生成嵌入的内容。传入单条字符串/消息视为单向量；传入多条视为批量。
        model: 要使用的嵌入模型名称，如果为None则使用默认模型。
        task: 生成意图
            (query检索词 / document目标文档 / similarity相似度 等)，将自动翻译到底层。
        dimensions: 强制降低返回的向量维度 (降维)。
        multimodal: 是否开启多模态嵌入提取。默认 False (极速安全的纯文本模式)。
        config: 嵌入配置对象。

    返回:
        EmbeddingResponse: 包含向量和 Token 消耗统计的富响应对象。
    """
    final_config = config or LLMEmbeddingConfig()
    if multimodal is not False:
        final_config.multimodal = multimodal

    from zhenxun.services.ai.message_builder import MessageBuilder

    batch = await MessageBuilder.normalize_to_embed_batch(
        input_batch, config=final_config
    )

    if not batch.payloads:
        return EmbeddingResponse(
            embeddings=[], usage=UsageInfo(), model_name=str(model)
        )

    if dimensions is not None:
        final_config.output_dimensionality = dimensions

    if task != "general":
        task_map = {
            "query": "RETRIEVAL_QUERY",
            "document": "RETRIEVAL_DOCUMENT",
            "similarity": "SEMANTIC_SIMILARITY",
            "classification": "CLASSIFICATION",
            "clustering": "CLUSTERING",
        }
        final_config.task_type = task_map.get(task)

    try:
        request = EmbeddingRequest(batch=batch, config=final_config)
        return await LLMOrchestrator.invoke(request, model_name=model, task="embedding")
    except LLMException as e:
        raise e.with_traceback(None) from None
    except Exception as e:
        friendly_msg = get_user_friendly_error_message(e)
        logger.error(f"文本嵌入失败: {e} | 建议: {friendly_msg}", e=e)
        raise UpstreamServerException(
            f"文本嵌入失败: {friendly_msg}",
            cause=e,
        ).with_traceback(None) from None


async def rerank(
    query: str,
    documents: list[str | dict[str, str]],
    top_n: int = 3,
    *,
    model: ModelName = None,
) -> list[RerankResult]:
    """
    无状态的文本重排便捷函数。

    参数:
        query: 用户查询问题
        documents: 候选文档列表 (支持纯文本或 {"image": "url", "text": "xxx"} 图文格式)
        top_n: 返回匹配度最高的前 n 个文档
        model: 重排模型名称 (如 BAAI/bge-reranker-v2-m3)
    """
    try:
        request = RerankRequest(query=query, documents=documents, top_n=top_n)
        response = await LLMOrchestrator.invoke(
            request, model_name=model, task="rerank"
        )
        return response.results
    except Exception as e:
        friendly_msg = get_user_friendly_error_message(e)
        logger.error(f"文档重排失败: {e} | 建议: {friendly_msg}", e=e)
        raise LLMException(f"文档重排失败: {friendly_msg}").with_traceback(
            None
        ) from None


async def generate_structured(
    message: PromptInput | list[LLMMessage],
    response_model: type[T],
    *,
    guardrails: list[GuardrailSource] | None = None,
    model: ModelName = None,
    config: GenerationConfig | IntentBuilder | None = None,
    max_retries: int | None = None,
    error_prompt_template: str | None = None,
    instruction: str | None = None,
    timeout: float | None = None,
) -> T:
    """
    请求大模型生成结构化数据，并自动验证/解析为指定的 Pydantic 模型。

    示例:
        class UserInfo(BaseModel):
            name: str
        info = await generate_structured("提取张三的信息", response_model=UserInfo)

    参数:
        message: 输入的消息内容，支持纯文本、UniMessage、消息对象列表等。
        response_model: 目标结构化输出的强类型 Pydantic 模型类。
        guardrails: 护栏来源列表，支持自然语言规则、自定义校验函数
        model: 强制指定调用的模型路由或名称，若为空则使用默认模型。
        config: 大模型生成的通用配置或意图构建器。
        max_retries: 格式解析或护栏校验失败时的最大自我反思重试次数（IVR），若为空则使用全局配置。
        error_prompt_template: 自定义校验失败时引导大模型自我修正的提示词模板。
        instruction: 注入到系统提示词中的全局任务指令或前置设定。
        timeout: 本次 API 请求的超时时间限制（秒）。

    返回:
        T: 解析验证通过后的 Pydantic 模型实例。
    """  # noqa: E501
    try:
        from zhenxun.services.ai.core.engine.structured_parser import (
            BaseOutputProcessor,
        )

        if max_retries is None:
            max_retries = get_llm_config().client_settings.structured_retries

        parsed_guardrails = parse_guardrails(guardrails)

        output_processor = BaseOutputProcessor(
            response_model=response_model,
            error_template=error_prompt_template,
        )
        json_schema = output_processor.get_json_schema()

        structured_config = GenerationConfig(
            output=OutputFormatConfig(
                response_format=ResponseFormat.JSON,
                response_schema=json_schema,
                structured_output_strategy=StructuredOutputStrategy.NATIVE,
            )
        )

        prompt_parts: list[str] = []
        if instruction:
            prompt_parts.append(instruction)

        schema_str = json.dumps(json_schema, ensure_ascii=False, indent=2)
        prompt_parts.append(
            "### ⚠️ [结构化输出要求]\n"
            "请严格按照以下 JSON Schema 格式进行回复，禁止包含任何额外纯文本解释：\n"
            f"```json\n{schema_str}\n```"
        )

        system_prompt = "\n\n".join(prompt_parts) if prompt_parts else None

        from zhenxun.services.ai.message_builder import MessageBuilder

        messages = await MessageBuilder.normalize_to_llm_messages(
            message if message is not None else [], instruction=system_prompt
        )

        if isinstance(config, IntentBuilder):
            config = config.build()

        final_config = (
            structured_config.merge_with(config) if config else structured_config
        )

        from zhenxun.services.ai.capabilities.builtin import (
            ReflexionCapability,
        )

        extra_context = {
            "output_processor": output_processor,
            "guardrails": parsed_guardrails,
            "max_retries": max_retries,
            "__sys_capabilities": [ReflexionCapability()],
        }

        response = await generate(
            messages=messages,
            model=model,
            config=final_config,
            timeout=timeout,
            extra=extra_context,
        )

        if not hasattr(response, "parsed_obj") or response.parsed_obj is None:
            raise LLMException("结构化输出失败：中间件未返回解析后的对象。")

        return response.parsed_obj
    except (LLMException, ModelRetry, ControlFlowExit) as e:
        raise e.with_traceback(None) from None
    except Exception as e:
        friendly_msg = get_user_friendly_error_message(e)
        logger.error(f"生成结构化响应失败: {e} | 建议: {friendly_msg}", e=e)
        raise LLMException(f"生成结构化响应失败: {friendly_msg}").with_traceback(
            None
        ) from None


async def generate(
    messages: list[LLMMessage],
    *,
    model: ModelName = None,
    config: GenerationConfig | IntentBuilder | None = None,
    timeout: float | None = None,
    extra: dict[str, Any] | None = None,
) -> ChatResponse:
    """
    [内部 API/高级用法] 直接传入底层消息实体列表生成响应。一般业务插件推荐使用 `chat`。

    参数:
        messages: 完整的消息历史列表，包括系统指令、用户消息和助手回复。
        model: 要使用的模型名称，如果为None则使用默认模型。
        config: (可选) 生成配置对象，将与默认配置合并后传递。

    返回:
        ChatResponse: 包含AI回复内容、使用信息和工具调用等的完整响应对象。
    """
    try:
        resolved_config: GenerationConfig | None = None
        if isinstance(config, IntentBuilder):
            resolved_config = config.build()
        else:
            resolved_config = config

        request = ChatRequest(
            messages=messages,
            config=resolved_config,
            timeout=timeout,
            extra=extra or {},
        )

        sys_caps = request.extra.pop("__sys_capabilities", [])
        run_ctx = request.extra.pop("run_context", None)

        if sys_caps:
            from zhenxun.services.ai.capabilities import CombinedCapability
            from zhenxun.services.ai.core.models import LLMContext
            from zhenxun.services.ai.run import RunContext

            run_context = run_ctx or RunContext()
            llm_context = LLMContext(request=request)
            combined_cap = CombinedCapability(sys_caps)

            async def inner_handler(
                ctx: LLMContext[ChatRequest, ChatResponse],
            ) -> ChatResponse:
                return await LLMOrchestrator.invoke(
                    ctx.request,
                    model_name=model,
                    task="chat",
                    override_config=resolved_config,
                )

            return await combined_cap.wrap_model_request(
                run_context, llm_context, inner_handler
            )
        else:
            return await LLMOrchestrator.invoke(
                request, model_name=model, task="chat", override_config=resolved_config
            )
    except (LLMException, ModelRetry, ControlFlowExit) as e:
        raise e.with_traceback(None) from None
    except Exception as e:
        friendly_msg = get_user_friendly_error_message(e)
        logger.error(f"生成响应失败: {e} | 建议: {friendly_msg}", e=e)
        raise LLMException(f"生成响应失败: {friendly_msg}").with_traceback(
            None
        ) from None


@overload
async def create_image(
    prompt: PromptInput,
    *,
    images: None = None,
    model: ModelName = None,
    config: GenerationConfig | IntentBuilder | None = None,
) -> ImageResponse:
    """根据文本提示生成一张新图片。"""
    ...


@overload
async def create_image(
    prompt: PromptInput,
    *,
    images: list[Path | bytes | str] | Path | bytes | str,
    model: ModelName = None,
    config: GenerationConfig | IntentBuilder | None = None,
) -> ImageResponse:
    """在给定图片的基础上，根据文本提示进行编辑或重新生成。"""
    ...


async def create_image(
    prompt: PromptInput,
    *,
    images: list[Path | bytes | str] | Path | bytes | str | None = None,
    model: ModelName = None,
    config: GenerationConfig | IntentBuilder | None = None,
) -> ImageResponse:
    """
    多模态图片生成/编辑函数。

    示例:
        res = await create_image("画一只猫", model="OpenAI/dall-e-3")
        img_bytes = res.images[0]

    说明:
        - 若 `images` 为 None，执行文本生成图片 (Text-to-Image)。
        - 若提供 `images`，执行图像编辑 (Image-to-Image)。
    """
    text_prompt = getattr(prompt, "extract_plain_text", lambda: str(prompt))()

    image_list = []
    if images:
        if isinstance(images, list):
            image_list.extend(images)
        else:
            image_list.append(images)

    if isinstance(config, IntentBuilder):
        config = config.build()
    config = config or GenerationConfig()

    try:
        request = ImageRequest(
            prompt=text_prompt,
            images=image_list if image_list else None,
            config=config,
        )
        return await LLMOrchestrator.invoke(
            request, model_name=model, task="image", override_config=config
        )
    except LLMException as e:
        raise e.with_traceback(None) from None
    except Exception as e:
        friendly_msg = get_user_friendly_error_message(e)
        logger.error(f"图片生成执行发生未知错误: {e} | 建议: {friendly_msg}", e=e)
        raise LLMException(f"图片生成失败: {friendly_msg}").with_traceback(
            None
        ) from None


async def create_speech(
    text: str,
    voice: str | None = None,
    *,
    model: ModelName = None,
    config: TTSConfig | None = None,
) -> AudioResponse:
    """
    通用文本转语音便捷函数。

    参数:
        text: 待合成的文本内容。
        voice: 快捷音色指定，若为空则自动使用目标模型的缺省最优音色。
        model: 指定生成语音的模型名称。
        config: 语音生成的额外设置。

    示例:
        res = await create_speech("你好，世界", voice="alloy", model="OpenAI/tts-1")
        Path("out.mp3").write_bytes(res.audio_bytes)
    """
    if not text:
        raise LLMException("TTS 输入文本不能为空")

    try:
        request = SpeechRequest(input_text=text, voice=voice, config=config)
        return await LLMOrchestrator.invoke(request, model_name=model, task="tts")
    except LLMException as e:
        raise e.with_traceback(None) from None
    except Exception as e:
        friendly_msg = get_user_friendly_error_message(e)
        logger.error(f"语音生成执行发生未知错误: {e} | 建议: {friendly_msg}", e=e)
        raise LLMException(f"语音生成失败: {friendly_msg}").with_traceback(
            None
        ) from None
