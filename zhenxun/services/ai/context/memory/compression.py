from abc import abstractmethod
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from zhenxun.services.ai.context.memory.models import MemoryConfig
from zhenxun.services.ai.core.engine.token_counter import token_counter
from zhenxun.services.ai.core.messages import (
    AudioPart,
    FilePart,
    ImagePart,
    LLMMessage,
    SystemMessage,
    TextPart,
    VideoPart,
)
from zhenxun.services.ai.llm.manager import get_default_model
from zhenxun.services.ai.utils.logger import log_memory as logger
from zhenxun.utils.pydantic_compat import model_copy

from .storage.interfaces import (
    BaseMemoryReducer,
)


class MultimodalPlaceholderReducer(BaseMemoryReducer):
    """视觉媒体降级：将超过一定轮数的老图片/视频替换为 <图片> 占位符文本"""

    def __init__(self, window_size: int = 5):
        """
        初始化多模态占位符修剪器。

        参数：
            window_size: 多模态视窗大小。在保留最近的指定数量的多模态消息对后，
            超出的旧消息中多模态内容（如图片、视频等）将被替换为占位文本。
        """
        self.window_size = window_size

    @staticmethod
    def apply_multimodal_placeholder(message: LLMMessage) -> LLMMessage:
        sanitized_message = model_copy(message, deep=False)
        new_content_parts = []

        for part in sanitized_message.content:
            if isinstance(part, ImagePart):
                new_content_parts.append(TextPart(text="<图片>"))
            elif isinstance(part, AudioPart):
                new_content_parts.append(TextPart(text="<音频>"))
            elif isinstance(part, VideoPart):
                new_content_parts.append(TextPart(text="<视频>"))
            elif isinstance(part, FilePart):
                new_content_parts.append(TextPart(text="<文件>"))
            elif isinstance(part, TextPart) and "[多模态内容:" in part.text:
                new_content_parts.append(TextPart(text="<图片>"))
            else:
                new_content_parts.append(part)

        merged_parts = []
        for part in new_content_parts:
            if (
                isinstance(part, TextPart)
                and merged_parts
                and isinstance(merged_parts[-1], TextPart)
            ):
                new_text = (merged_parts[-1].text or "") + " " + (part.text or "")
                merged_parts[-1] = TextPart(text=new_text.strip())
            else:
                merged_parts.append(part)

        sanitized_message.content = merged_parts
        sanitized_message.token_cost = None
        return sanitized_message

    async def reduce(self, messages, current_tokens, model_name, base_overhead=0):
        if self.window_size <= 0:
            return messages, False, current_tokens

        processed_messages = []
        user_multimodal_count = 0
        changed = False

        for msg in reversed(messages):
            has_multimodal = False
            if isinstance(msg.content, list):
                has_multimodal = any(
                    isinstance(p, ImagePart | AudioPart | VideoPart | FilePart)
                    or (isinstance(p, TextPart) and "[多模态内容:" in p.text)
                    for p in msg.content
                )

            if has_multimodal:
                if msg.role == "user":
                    user_multimodal_count += 1
                if user_multimodal_count > self.window_size:
                    processed_messages.append(self.apply_multimodal_placeholder(msg))
                    changed = True
                else:
                    processed_messages.append(msg)
            else:
                processed_messages.append(msg)

        if not changed:
            return messages, False, current_tokens

        processed_messages.reverse()
        new_tokens = token_counter.count_context(
            processed_messages, model_name, base_overhead
        )
        return processed_messages, True, new_tokens


class MessageDropper(BaseMemoryReducer):
    """消息丢弃器：在 Token 超过阈值时丢弃最早的非置顶消息对。"""

    def __init__(self, trigger_tokens: int = 4000):
        """
        初始化消息丢弃器。

        参数：
            trigger_tokens: 触发丢弃策略的 Token 阈值上限。
            当当前对话 Token 总数超过此值时，将触发硬截断。
        """
        self.trigger_tokens = trigger_tokens

    async def reduce(self, messages, current_tokens, model_name, base_overhead=0):
        if current_tokens <= self.trigger_tokens:
            return messages, False, current_tokens

        logger.info(
            "✂️ 触发硬截断丢弃策略 | 原因: "
            f"当前 Token 预估 ({current_tokens}) 仍超过硬性上限 ({self.trigger_tokens})，"  # noqa: E501
            "开始丢弃最旧的历史对话..."
        )
        new_messages = list(messages)
        changed = False

        while current_tokens > self.trigger_tokens:
            user_indices = [
                i
                for i, m in enumerate(new_messages)
                if m.role == "user"
                and not (m.metadata and m.metadata.get("pinned", False))
            ]

            if len(user_indices) < 2:
                break

            start_idx = user_indices[0]
            end_idx = user_indices[1]

            del new_messages[start_idx:end_idx]
            changed = True

            current_tokens = token_counter.count_context(
                new_messages, model_name, base_overhead
            )
        return new_messages, changed, current_tokens


class ToolPrunerReducer(BaseMemoryReducer):
    """工具结果修剪器：纯粹计算工具输出的 Token 和轮数，超标时剔除老旧工具返回结果"""

    def __init__(
        self,
        keep_recent_turns: int = 3,
        trigger_tokens: int = 4000,
        max_turns: int = 0,
    ):
        """
        初始化工具结果修剪器。

        参数：
            keep_recent_turns: 保留最近的工具调用轮数（不进行内容截断的轮数）。
            trigger_tokens: 触发工具修剪策略的工具总 Token 阈值上限。
            max_turns: 触发工具修剪的最大工具调用轮数上限。若为 0，则不限制轮数。
        """
        self.keep_recent_turns = keep_recent_turns
        self.trigger_tokens = trigger_tokens
        self.max_turns = max_turns

    async def reduce(self, messages, current_tokens, model_name, base_overhead=0):
        tool_msgs = [m for m in messages if m.role == "tool"]
        tool_turns = len(tool_msgs)

        if tool_turns == 0:
            return messages, False, current_tokens

        tool_tokens = sum(token_counter.count_message(m, model_name) for m in tool_msgs)

        is_token_exceeded = tool_tokens > self.trigger_tokens
        is_turn_exceeded = self.max_turns > 0 and tool_turns > self.max_turns

        if not (is_token_exceeded or is_turn_exceeded):
            return messages, False, current_tokens

        reasons = []
        if is_token_exceeded:
            reasons.append(f"工具Token超标 ({tool_tokens} > {self.trigger_tokens})")
        if is_turn_exceeded:
            reasons.append(f"工具调用轮数超限 ({tool_turns} > {self.max_turns})")

        logger.info(f"✂️ 触发工具结果修剪策略 | 原因: {' 且 '.join(reasons)}")

        from zhenxun.services.ai.core.messages import ToolReturnPart

        new_messages = []
        tools_kept = 0
        changed = False

        for msg in reversed(messages):
            if msg.role != "tool":
                new_messages.append(msg)
                continue

            if tools_kept < self.keep_recent_turns:
                tools_kept += 1
                new_messages.append(msg)
                continue

            new_content = []
            part_changed = False
            for p in msg.content:
                if isinstance(p, ToolReturnPart):
                    old_len = len(str(p.output))
                    new_p = model_copy(
                        p,
                        update={
                            "output": f"[数据过载自动截断 - 原长度: {old_len} 字符]"
                        },
                    )
                    new_content.append(new_p)
                    part_changed = True
                    changed = True
                else:
                    new_content.append(p)

            if part_changed:
                new_msg = model_copy(
                    msg, update={"content": new_content, "token_cost": None}
                )
                new_messages.append(new_msg)
            else:
                new_messages.append(msg)

        if not changed:
            return messages, False, current_tokens

        new_messages.reverse()
        new_total = token_counter.count_context(new_messages, model_name, base_overhead)
        return new_messages, True, new_total


class AbstractSummarizerReducer(BaseMemoryReducer):
    """抽象总结压缩器：提取阈值判断与上下文分流的公共逻辑"""

    def __init__(
        self,
        strategy_name: str,
        keep_recent_turns: int = 0,
        trigger_tokens: int = 4000,
        max_turns: int | None = None,
        summarization_model: str | None = None,
    ):
        """
        初始化抽象总结压缩基类。

        参数：
            strategy_name: 压缩策略的名称，用于日志输出和追踪。
            keep_recent_turns: 压缩时需要保留的最新的对话轮数（不参与总结的轮数）。
            trigger_tokens: 触发总结策略的 Token 阈值上限。
            max_turns: 触发总结策略的最大对话轮数上限。
            summarization_model: 用于执行总结压缩大模型请求的模型名称，若为 None 则使用默认模型。
        """  # noqa: E501
        self.strategy_name = strategy_name
        self.keep_recent_turns = keep_recent_turns
        self.trigger_tokens = trigger_tokens
        self.max_turns = max_turns
        self.summarization_model = summarization_model

    @abstractmethod
    async def _execute_summarization(
        self, to_summarize: list[LLMMessage], prev_summary: str
    ) -> LLMMessage | None:
        """由子类实现具体的 LLM 调用逻辑，返回新的总结消息"""
        pass

    async def reduce(self, messages, current_tokens, model_name, base_overhead=0):
        user_turns = sum(
            1
            for m in messages
            if m.role == "user"
            and not (m.metadata and m.metadata.get("is_summary", False))
        )
        is_token_exceeded = current_tokens > self.trigger_tokens
        is_turn_exceeded = (
            self.max_turns is not None
            and self.max_turns > 0
            and user_turns > self.max_turns
        )

        if not (is_token_exceeded or is_turn_exceeded):
            return messages, False, current_tokens

        reasons = []
        if is_token_exceeded:
            reasons.append(f"Token 预估超限 ({current_tokens} > {self.trigger_tokens})")
        if is_turn_exceeded:
            reasons.append(f"有效对话轮次超限 ({user_turns} > {self.max_turns})")
        logger.info(
            f"🔄 [MemoryCompression] 触发{self.strategy_name}策略 | 原因: "
            f"{' 且 '.join(reasons)}"
        )

        pinned_msgs, working_msgs, prev_summary = [], [], ""
        for msg in messages:
            is_pinned = isinstance(msg, SystemMessage) or (
                msg.metadata and msg.metadata.get("pinned", False)
            )
            if msg.metadata and msg.metadata.get("is_summary", False):
                prev_summary = msg.extract_text
            elif is_pinned:
                pinned_msgs.append(msg)
            else:
                working_msgs.append(msg)

        user_indices = [i for i, m in enumerate(working_msgs) if m.role == "user"]

        if len(user_indices) <= self.keep_recent_turns:
            return messages, False, current_tokens

        split_idx = (
            user_indices[-self.keep_recent_turns]
            if self.keep_recent_turns > 0
            else len(working_msgs)
        )
        to_summarize = working_msgs[:split_idx]
        to_keep = working_msgs[split_idx:]

        new_summary_msg = await self._execute_summarization(to_summarize, prev_summary)
        if not new_summary_msg:
            return messages, False, current_tokens

        new_messages = [*pinned_msgs, new_summary_msg, *to_keep]
        return (
            new_messages,
            True,
            token_counter.count_context(new_messages, model_name, base_overhead),
        )


class LLMSummarizerReducer(AbstractSummarizerReducer):
    """大模型总结压缩器：将较早的历史对话记录通过 LLM 压缩合并为一段文本摘要。"""

    def __init__(
        self,
        keep_recent_turns: int = 0,
        trigger_tokens: int = 4000,
        max_turns: int | None = None,
        summarization_model: str | None = None,
        summarization_prompt: str = (
            "请概括以下对话内容，保留关键的约束条件、用户偏好、"
            "已完成的任务状态和未解决的问题。"
        ),
    ):
        """
        初始化大模型总结压缩器。

        参数：
            keep_recent_turns: 压缩时需要保留的最新的对话轮数。
            trigger_tokens: 触发总结策略的 Token 阈值上限。
            max_turns: 触发总结策略的最大对话轮数上限。
            summarization_model: 用于执行总结压缩的大模型名称。
            summarization_prompt: 发送给大模型的总结引导 Prompt 提示词。
        """
        super().__init__(
            strategy_name="历史对话合并总结",
            keep_recent_turns=keep_recent_turns,
            trigger_tokens=trigger_tokens,
            max_turns=max_turns,
            summarization_model=summarization_model,
        )
        self.summarization_prompt = summarization_prompt

    async def _execute_summarization(
        self, to_summarize: list[LLMMessage], prev_summary: str
    ) -> LLMMessage | None:
        prompt_text = f"### 📋 [对话摘要任务]\n{self.summarization_prompt}\n\n"
        if prev_summary:
            prompt_text += "####  prev_summary (参考先前的快照):\n"
            prompt_text += f"> {prev_summary}\n\n"
        prompt_text += "#### 待处理的历史消息流：\n"
        for m in to_summarize:
            c_str = m.extract_text[:1500]
            speaker = m.source_name if m.source_name else m.role.capitalize()
            prompt_text += f"[{speaker}]: {c_str}\n"
        prompt_text += "</需要合并的旧对话记录>\n"

        from zhenxun.services.ai.llm.api import chat

        try:
            model_to_use = self.summarization_model or get_default_model("chat")
            response = await chat(
                prompt_text,
                model=model_to_use,
                instruction="你是后台记忆整理引擎。请客观、简明输出当前对话全局摘要。",
            )
            new_summary_msg = LLMMessage.assistant_text_response(
                f"【历史对话摘要记忆】\n{response.text}"
            )
            new_summary_msg.metadata = {"is_summary": True, "pinned": True}
            return new_summary_msg
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] 压缩总结调用失败，已跳过本次压缩: {e}"
            )
            return None


_T_Summary = TypeVar("_T_Summary", bound=BaseModel)


class StructuredSummaryReducer(AbstractSummarizerReducer, Generic[_T_Summary]):
    """结构化总结压缩器：基于 JSON Schema 格式化抽取长上下文状态信息并合并"""

    def __init__(
        self,
        response_model: type[_T_Summary],
        prompt_template: str,
        format_callback: Callable[[_T_Summary], str],
        keep_recent_turns: int = 0,
        trigger_tokens: int = 4000,
        max_turns: int | None = None,
        summarization_model: str | None = None,
        instruction: str = (
            "请提取并合并先前的状态和最新的对话内容，保持精简，不要编造事实"
        ),
    ):
        """
        初始化结构化总结压缩器。

        参数：
            response_model: 接收结构化输出的 Pydantic 模型类，需继承自 BaseModel。
            prompt_template: 用于抽取合并状态的 Prompt 模板，包含 {prev_summary} 和 {dialogue} 占位符。
            format_callback: 格式化回调函数，用于将结构化 Pydantic 响应对象转换为便于大模型阅读的字符串。
            keep_recent_turns: 压缩时需要保留的最新的对话轮数。
            trigger_tokens: 触发总结策略的 Token 阈值上限。
            max_turns: 触发总结策略的最大对话轮数上限。
            summarization_model: 用于执行总结压缩的大模型名称。
            instruction: 指导大模型生成结构化数据时的系统指令说明。
        """  # noqa: E501
        super().__init__(
            strategy_name="结构化状态抽取压缩",
            keep_recent_turns=keep_recent_turns,
            trigger_tokens=trigger_tokens,
            max_turns=max_turns,
            summarization_model=summarization_model,
        )
        self.response_model = response_model
        self.prompt_template = prompt_template
        self.format_callback = format_callback
        self.instruction = instruction

    async def _execute_summarization(
        self, to_summarize: list[LLMMessage], prev_summary: str
    ) -> LLMMessage | None:
        dialogue_text = ""
        for m in to_summarize:
            c_str = m.extract_text[:1500]
            speaker = m.source_name if m.source_name else m.role.capitalize()
            dialogue_text += f"[{speaker}]: {c_str}\n"

        prompt_text = self.prompt_template.format(
            prev_summary=prev_summary, dialogue=dialogue_text
        )

        from zhenxun.services.ai.llm.api import generate_structured

        try:
            model_to_use = self.summarization_model or get_default_model("chat")
            summary_obj = await generate_structured(
                prompt_text,
                response_model=self.response_model,
                model=model_to_use,
                instruction=self.instruction,
            )

            summary_text = self.format_callback(summary_obj)

            new_summary_msg = LLMMessage.assistant_text_response(
                f"【历史状态摘要记忆】\n{summary_text}"
            )
            new_summary_msg.metadata = {"is_summary": True, "pinned": True}
            return new_summary_msg
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] 结构化总结失败，已跳过本次压缩: {e}"
            )
            return None


class CondenserPipeline:
    """上下文压缩流水线：按顺序依次执行各阶段的记忆压缩减项。"""

    def __init__(self, reducers: list[BaseMemoryReducer]):
        """
        初始化上下文压缩流水线。

        参数：
            reducers: 压缩减项器列表，将按顺序对记忆进行多阶段修剪和压缩。
        """
        self.reducers = reducers

    @classmethod
    def create_from_configs(
        cls, memory_config: MemoryConfig | None, model_name: str
    ) -> "CondenserPipeline":
        """基于全局和局部配置组装压缩管线工厂方法"""
        from zhenxun.services.ai.config import get_llm_config
        from zhenxun.services.ai.llm.system.capabilities import get_model_capabilities

        config = get_llm_config().context_settings
        pipeline_reducers = []
        caps = get_model_capabilities(model_name)

        vw = config.vision_window_size
        if memory_config and memory_config.compression.vision_window is not None:
            vw = memory_config.compression.vision_window
        if vw > 0:
            pipeline_reducers.append(MultimodalPlaceholderReducer(window_size=vw))

        tp = config.tool_pruning
        if tp.enable:
            tp_limit = (
                int(caps.max_input_tokens * tp.trigger_threshold)
                if tp.trigger_threshold <= 1.0
                else int(tp.trigger_threshold)
            )
            pipeline_reducers.append(
                ToolPrunerReducer(
                    keep_recent_turns=tp.keep_recent_turns,
                    trigger_tokens=tp_limit,
                    max_turns=tp.max_history_turns,
                )
            )

        policy = memory_config.compression.policy if memory_config else None
        if policy is not None:
            pipeline_reducers.extend(policy)
        else:
            threshold = config.llm_summary.trigger_threshold
            if memory_config and memory_config.compression.threshold is not None:
                threshold = memory_config.compression.threshold

            limit = (
                int(caps.max_input_tokens * threshold)
                if threshold <= 1.0
                else int(threshold)
            )

            max_turns = config.llm_summary.max_history_turns
            if (
                memory_config
                and memory_config.compression.max_history_turns is not None
            ):
                max_turns = memory_config.compression.max_history_turns

            if config.llm_summary.enable:
                pipeline_reducers.extend(
                    MemoryPolicy.llm_summarize(
                        trigger_tokens=limit,
                        max_turns=max_turns,
                        keep_recent_turns=config.llm_summary.keep_recent_turns,
                        summarization_model=config.llm_summary.summarization_model,
                        summarization_prompt=config.llm_summary.summarization_prompt,
                    )
                )
            else:
                pipeline_reducers.extend(MemoryPolicy.unlimited())

        return cls(pipeline_reducers)

    async def run(
        self, messages, model_name, base_overhead=0
    ) -> tuple[list[LLMMessage], bool]:
        current_tokens = token_counter.count_context(
            messages, model_name, base_overhead
        )

        current_messages = messages
        any_changed = False
        for reducer in self.reducers:
            current_messages, changed, current_tokens = await reducer.reduce(
                current_messages,
                current_tokens,
                model_name,
                base_overhead,
            )
            if changed:
                any_changed = True
        return current_messages, any_changed


class MemoryPolicy:
    """
    记忆策略工厂。
    为开发者提供开箱即用的上下文压缩管线组装方案。
    """

    @staticmethod
    def unlimited() -> list[BaseMemoryReducer]:
        """无限制模式。不进行任何形式的截断和总结，适用于短对话或纯 Agent 内部流转。"""
        return []

    @staticmethod
    def llm_summarize(
        trigger_tokens: int = 4000,
        max_turns: int | None = None,
        keep_recent_turns: int = 0,
        summarization_model: str | None = None,
        summarization_prompt: str = (
            "请概括以下对话内容，保留关键的约束条件、用户偏好、"
            "已完成的任务状态和未解决的问题。"
        ),
    ) -> list[BaseMemoryReducer]:
        """LLM 总结压缩模式。Token 达标后，自动将历史对话合并为一段 Summary。"""
        return [
            LLMSummarizerReducer(
                keep_recent_turns=keep_recent_turns,
                trigger_tokens=trigger_tokens,
                max_turns=max_turns,
                summarization_model=summarization_model,
                summarization_prompt=summarization_prompt,
            ),
            MessageDropper(trigger_tokens=trigger_tokens),
        ]

    @staticmethod
    def structured_summarize(
        trigger_tokens: int = 4000,
        max_turns: int | None = None,
        keep_recent_turns: int = 0,
        summarization_model: str | None = None,
        response_model: type[BaseModel] | None = None,
        prompt_template: str | None = None,
        format_callback: Callable[[Any], str] | None = None,
    ) -> list[BaseMemoryReducer]:
        """结构化总结压缩模式。使用 JSON Schema 强制大模型提取核心状态。"""

        class DefaultStateSummary(BaseModel):
            user_context: str = Field(
                description="用户的核心意图、诉求、人设或长期记忆规则。"
            )
            completed_tasks: str = Field(description="已完成的操作或已经确认的情节。")
            pending_tasks: str = Field(description="正在进行中的任务或尚未解答的问题。")
            current_state: str = Field(
                description="当前状态，如重要变量、玩家血量、关键物品坐标等。"
            )

        def default_format(obj: DefaultStateSummary) -> str:
            return (
                f"👤 用户上下文: {obj.user_context}\n"
                f"✅ 已完成/确认: {obj.completed_tasks}\n"
                f"⏳ 待处理/疑问: {obj.pending_tasks}\n"
                f"📌 当前状态: {obj.current_state}"
            )

        default_prompt = (
            "你是一个专门用于长上下文状态压缩的引擎。请阅读以下先前的总结和旧对话，"
            "提取核心状态信息，并合并它们。\n\n"
            "<之前的状态摘要>\n{prev_summary}\n</之前的状态摘要>\n\n"
            "<需要合并的旧对话记录>\n"
            "{dialogue}"
            "</需要合并的旧对话记录>\n"
        )

        return [
            StructuredSummaryReducer(
                response_model=response_model or DefaultStateSummary,
                prompt_template=prompt_template or default_prompt,
                format_callback=format_callback or default_format,
                keep_recent_turns=keep_recent_turns,
                trigger_tokens=trigger_tokens,
                max_turns=max_turns,
                summarization_model=summarization_model,
            ),
            MessageDropper(trigger_tokens=trigger_tokens),
        ]
