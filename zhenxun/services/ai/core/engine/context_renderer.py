from collections.abc import Sequence
from typing import Any

from zhenxun.services.ai.core.messages import AgentEvent, AgentMessage, LLMMessage
from zhenxun.services.log import logger


class ContextConverter:
    """
    上下文边界降维转换器。
    负责将内存中混合了 AgentEvent 与 LLMMessage 的业务事件流，
    安全拍平为底层大模型可读的原生 API 载体。
    """

    @staticmethod
    def flatten_to_llm_messages(
        messages: Sequence[AgentMessage], context: Any | None = None
    ) -> list[LLMMessage]:
        """
        将包含 AgentEvent 和 LLMMessage 的混合消息序列，扁平化转换为大模型 API 专用的 LLMMessage 列表。

        参数：
            messages: 混合了 LLMMessage 和 AgentEvent 对象的原始业务消息序列。
            context: 渲染 AgentEvent 所需的运行时上下文对象（如 RunContext）。

        返回：
            list[LLMMessage]: 扁平化转换后生成的纯底层大模型原生消息列表。
        """  # noqa: E501
        flattened: list[LLMMessage] = []

        for msg in messages:
            if isinstance(msg, LLMMessage):
                flattened.append(msg)
            elif isinstance(msg, AgentEvent):
                try:
                    res = msg.to_llm_message(context)
                    if res is None:
                        continue

                    if isinstance(res, str):
                        flattened.append(LLMMessage.system(res))
                    elif isinstance(res, LLMMessage):
                        flattened.append(res)
                    elif isinstance(res, list):
                        flattened.extend(res)
                    else:
                        logger.warning(
                            f"事件 {msg.__class__.__name__} 的 to_llm_message "
                            f"返回了不支持的类型: {type(res)}"
                        )
                except Exception as e:
                    logger.error(
                        f"业务事件 [{msg.__class__.__name__}] "
                        f"在降维渲染为大模型 Prompt 时发生崩溃: {e}\n"
                        f"防呆拦截：请检查该事件 to_llm_message 方法的实现。"
                    )
            else:
                logger.warning(
                    f"ContextConverter 遇到未知类型的消息，已跳过: {type(msg)}"
                )

        return flattened
