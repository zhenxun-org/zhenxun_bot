from pathlib import Path
from typing import Any, Literal

import anyio
from nonebot.adapters import Bot, Event
from pydantic import BaseModel, Field

from zhenxun.services.ai.context.rag.backends import StorageBackend
from zhenxun.services.ai.context.rag.engine import ScopedRAGClient
from zhenxun.services.ai.context.rag.models import BaseRecord
from zhenxun.services.ai.core.messages import LLMMessage
from zhenxun.services.ai.llm.api import generate_structured
from zhenxun.services.ai.run import RunContext
from zhenxun.services.ai.tools.core.decorators import tool
from zhenxun.services.ai.tools.models import ToolkitConfig, ToolResult
from zhenxun.services.ai.utils.logger import log_knowledge as logger

from .base import BaseKnowledge
from .readers import (
    BaseReader,
    CSVReader,
    TextReader,
)


class QueryAnalysis(BaseModel):
    """大模型结构化提取查询意图"""

    keywords: list[str] = Field(
        description="提取出1~3个极其简短的搜索短语或名词，严格去除所有客套话、修饰词和标点。如果用户意图跨度较大，可以拆分为多个短语。"
    )


class VectorKnowledge(BaseKnowledge):
    """
    原生语义向量知识库。
    将长文档切分、向量化并存入关系型/向量数据库，
    向大模型提供语义检索 (Semantic Search) 工具。
    """

    default_instructions = (
        "## 语义知识库\n"
        "你拥有访问外部语义向量知识库的权限。请遵循以下规则：\n"
        "1. **优先检索**：在回答专业或背景问题时，务必使用 `search_knowledge` 工具。\n"
        "2. **语义搜索**：你可以直接输入完整的问题或描述作为检索词，"
        "系统会自动进行语义匹配。\n"
        "3. **精确过滤**：如果你需要查阅特定范围，可以在 filters 参数中"
        "传入 JSON 字典进行精确匹配（如 {'source': 'local_file'}）。\n"
        "4. **基于事实**：必须仅根据检索到的内容回答，严禁编造信息。"
    )

    default_auto_inject_template = (
        "### 📚 [本地知识库自动检索结果]\n"
        "基于用户的最新提问，系统后台已自动为你检索了以下参考资料。"
        "请你务必优先结合以下资料回答用户的问题，严禁编造：\n\n"
        "{knowledge_text}"
    )

    _global_storage: StorageBackend | None = None

    def __init__(
        self,
        rag_client: ScopedRAGClient | None = None,
        injection_mode: Literal["tool", "auto", "smart"] = "tool",
        query_rewrite_model: str | None = None,
        auto_inject_template: str | None = None,
        query_rewrite_prompt: str | None = None,
        query_rewrite_instruction: str | None = None,
        search_limit: int = 8,
        inject_limit: int = 12,
        **kwargs: Any,
    ):
        """
        初始化向量语义知识库工具箱。

        参数:
            rag_client: RAG 基础设施客户端实例，默认 None。
            injection_mode: 知识库的介入模式。
                - "tool": 纯工具模式 (默认)。大模型需自主思考并显式调用 `search_knowledge` 工具获取信息。
                - "auto": 自动注入模式。向大模型隐藏检索工具，直接使用用户的原始输入去数据库粗筛并静默注入。
                - "smart": 智能查询模式。向大模型隐藏检索工具，先利用 LLM 对用户的提问进行改写，再查库注入，准确率最高。
            query_rewrite_model: 在 "smart" 模式下，指定用于重写查询词的大模型名称（为空则跟随当前主模型）。
            auto_inject_template: 自动/智能注入模式下向大模型提示词注入的模板字符串，默认 None。
            query_rewrite_prompt: 智能模式下对查询词进行改写时的提示词，默认 None。
            query_rewrite_instruction: 智能模式下进行查询词改写的大模型 System 提示词说明，默认 None。
            search_limit: 单次库检索的返回记录数限制，默认 8。
            inject_limit: 最终合并去重后注入给大模型的上下文片段数上限，默认 12。
            **kwargs: 传递给父类的其他关键字参数。
        """  # noqa: E501
        self.injection_mode = injection_mode
        self.query_rewrite_model = query_rewrite_model
        self.auto_inject_template = (
            auto_inject_template or self.default_auto_inject_template
        )
        self.query_rewrite_prompt = (
            query_rewrite_prompt
            or "用户原始提问：{query}\n\n请提取核心搜索词用于专业知识库向量检索。"
        )
        self.query_rewrite_instruction = (
            query_rewrite_instruction or "你是一个资深的数据检索架构师。"
        )
        self.search_limit = search_limit
        self.inject_limit = inject_limit

        if injection_mode in ("auto", "smart"):
            config = kwargs.get("config")
            if not config:
                config = ToolkitConfig()
                kwargs["config"] = config
            if config.exclude is None:
                config.exclude = []
            config.exclude.append("search_knowledge")

        super().__init__(**kwargs)

        if rag_client is None:
            from zhenxun.services.ai.context.rag.backends import DictStorageBackend
            from zhenxun.services.ai.context.rag.builder import RAGBuilder

            rag_client = RAGBuilder(DictStorageBackend()).build()

        self.rag_client = rag_client
        self.readers: dict[str, BaseReader] = {}

        txt_reader = TextReader()
        for ext in [".txt", ".md", ".json", ".log", ".yaml", ".yml", ".ini"]:
            self.readers[ext] = txt_reader
        self.readers[".csv"] = CSVReader()

    @classmethod
    def from_event(
        cls,
        event: Event | None = None,
        bot: Bot | None = None,
        isolation: Literal["group", "user"] = "group",
        **kwargs,
    ) -> "VectorKnowledge":
        """
        根据 NoneBot 的 Event 自动推导并创建一个物理隔离的向量知识库实例。
        """
        from zhenxun.services.ai.context.memory.types import Isolation
        from zhenxun.services.ai.context.rag.backends import DictStorageBackend
        from zhenxun.services.ai.context.rag.builder import RAGBuilder
        from zhenxun.services.ai.run.context import NoneBotDeps
        from zhenxun.services.ai.utils import ContextUtils

        if not bot or not event:
            deps = NoneBotDeps.get_current()
            bot = bot or (deps.bot if deps else None)
            event = event or (deps.event if deps else None)

        if not bot or not event:
            raise ValueError(
                "无法隐式获取当前对话的 Bot 或 Event 上下文,"
                "如果您在定时任务或后台线程中使用，请显式传入 bot 和 event 参数。"
            )

        scope_builder = (
            Isolation.GROUP_SHARED()
            if isolation == "group"
            else Isolation.USER_GLOBAL()
        )
        session_meta = ContextUtils.generate_session_meta(
            bot=bot, event=event, scope_builder=scope_builder, namespace="auto_kb"
        )

        if cls._global_storage is None:
            cls._global_storage = DictStorageBackend()

        client = (
            RAGBuilder(cls._global_storage)
            .with_scope(session_meta.accessible_scopes)
            .build()
        )
        return cls(rag_client=client, **kwargs)

    def register_reader(
        self, ext: str | list[str], reader: BaseReader
    ) -> "VectorKnowledge":
        """挂载自定义后缀文件解析器 (如 PDF, Docx)，支持链式调用"""
        exts = [ext] if isinstance(ext, str) else ext
        for e in exts:
            e = e.lower()
            if not e.startswith("."):
                e = f".{e}"
            self.readers[e] = reader
        return self

    def get_instructions(self) -> str | None:
        """
        如果是自动/智能注入模式，对大模型完全隐藏检索提示词，防止其误调用。
        """
        if self.injection_mode != "tool":
            return None
        return super().get_instructions()

    async def before_llm_request(
        self, context: RunContext, messages: list[LLMMessage]
    ) -> None:
        """
        生命周期钩子：在向底层 LLM 发起请求前触发。
        负责执行 "auto" 或 "smart" 模式下的前置主动检索与上下文注入。
        """
        if self.injection_mode == "tool":
            return

        user_input = context.run.user_input
        if not user_input:
            return

        queries_to_search = [user_input]

        if self.injection_mode == "smart":
            try:
                model_to_use = self.query_rewrite_model or context.run.current_model
                prompt = self.query_rewrite_prompt.format(query=user_input)
                res = await generate_structured(
                    message=prompt,
                    response_model=QueryAnalysis,
                    model=model_to_use,
                    instruction=self.query_rewrite_instruction,
                )
                if res.keywords:
                    logger.info(
                        f"✨ [Smart Knowledge] 搜索词改写成功: '{user_input}' -> "
                        f"{res.keywords}"
                    )
                    queries_to_search = res.keywords
            except Exception as e:
                logger.warning(f"[Smart Knowledge] Query 改写失败，降级使用原词: {e}")

        all_results = []
        seen_ids = set()
        for q in queries_to_search:
            results = await self.rag_client.search(query=q, limit=self.search_limit)
            for res in results:
                if res.record.id not in seen_ids:
                    seen_ids.add(res.record.id)
                    all_results.append(res)

        if not all_results:
            return

        all_results.sort(key=lambda x: x.score, reverse=True)
        all_results = all_results[: self.inject_limit]

        formatted_results = []
        for result in all_results:
            doc_name = result.record.metadata.get("name", "未命名文档")
            formatted_results.append(
                f"📄 来源: {doc_name}\n内容片段:\n{result.record.content}"
            )

        knowledge_text = "\n\n======\n\n".join(formatted_results)
        system_prompt = self.auto_inject_template.format(knowledge_text=knowledge_text)

        messages.insert(0, LLMMessage.system(system_prompt))

    async def add_document(self, document: BaseRecord) -> int:
        """
        通过注入的 Ingestion Pipeline 处理并入库文档
        返回成功入库的 Chunk 数量。
        """
        return await self.rag_client.ingest([document])

    async def add_file(self, file_path: str | Path) -> int:
        """
        读取并注入单个文件。
        """
        aio_path = anyio.Path(file_path)
        std_path = Path(file_path)
        if not await aio_path.is_file():
            logger.error(f"[VectorKnowledge] 文件不存在: {std_path}")
            return 0

        ext = std_path.suffix.lower()
        reader = self.readers.get(ext)
        if not reader:
            logger.warning(f"当前知识库未配置支持解析文件后缀: {ext}")
            return 0

        doc = await reader.read(std_path)
        if not doc:
            return 0

        return await self.rag_client.ingest([doc])

    async def add_directory(self, dir_path: str | Path) -> int:
        """扫描目录并批量注入所有支持的文件"""
        aio_path = anyio.Path(dir_path)
        docs_to_ingest = []

        async for p in aio_path.rglob("*"):
            if await p.is_file():
                std_path = Path(p)
                ext = std_path.suffix.lower()
                reader = self.readers.get(ext)
                if not reader:
                    logger.warning(f"当前知识库未配置支持解析文件后缀: {ext}")
                    continue

                doc = await reader.read(std_path)
                if doc:
                    docs_to_ingest.append(doc)

        if not docs_to_ingest:
            return 0

        return await self.rag_client.ingest(docs_to_ingest)

    @tool(
        name="search_knowledge",
        description=(
            "在语义知识库中搜索最相关的内容片段。可以通过 filters 字典进行额外过滤。"
        ),
    )
    async def search_knowledge(
        self, query: str, filters: dict[str, Any] | None = None, limit: int = 5
    ) -> ToolResult:
        results = await self.rag_client.search(
            query=query, limit=limit, metadata_filters=filters
        )

        if not results:
            return ToolResult(output=f"知识库中未找到与 '{query}' 紧密相关的内容。")

        formatted_results = []
        for result in results:
            doc_name = result.record.metadata.get("name", "未命名文档")
            formatted_results.append(
                f"📄 来源: {doc_name}\n片段内容:\n{result.record.content}"
            )

        final_text = "\n\n======\n\n".join(formatted_results)
        return ToolResult(output=final_text)
