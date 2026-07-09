from abc import ABC, abstractmethod
import asyncio
import re

from zhenxun.services.ai.utils.logger import log_rag as logger

from .models import BaseRecord
from .utils import cosine_similarity


class ChunkingStrategy(ABC):
    """分块策略抽象基类，用于将长文本记录切分为多个短的 BaseRecord"""

    @abstractmethod
    def chunk(self, record: BaseRecord) -> list[BaseRecord]:
        """将输入的记录切分为子记录列表。由子类具体实现"""
        raise NotImplementedError

    def clean_text(self, text: str) -> str:
        """清洗和规范化文本，去除多余的空行和空白字符"""
        cleaned_text = re.sub(r"\n+", "\n", text)
        cleaned_text = re.sub(r"[ \t]+", " ", cleaned_text)
        return cleaned_text.strip()

    def _create_chunk_record(
        self, original_record: BaseRecord, chunk_number: int, content: str
    ) -> BaseRecord:
        """根据原始记录创建分块后的 BaseRecord，并自动附带切片索引和父级ID等元数据"""
        meta_data = original_record.metadata.copy()
        meta_data["chunk_index"] = chunk_number
        meta_data["chunk_size"] = len(content)
        meta_data["parent_id"] = original_record.id
        return BaseRecord(
            id=f"{original_record.id}_{chunk_number}",
            content=content,
            metadata=meta_data,
        )


class DocumentChunking(ChunkingStrategy):
    """段落语义分块策略 (按双换行切分)"""

    def __init__(self, chunk_size: int = 1000):
        """
        初始化段落语义分块策略。

        参数:
            chunk_size: 单个分块的最大字符长度限制，默认 1000。
        """
        self.chunk_size = chunk_size

    def chunk(self, record: BaseRecord) -> list[BaseRecord]:
        """按双换行将内容切分为段落，并将相邻段落合并为符合最大字符长度限制的分块"""
        if len(record.content) <= self.chunk_size:
            return [
                self._create_chunk_record(record, 0, self.clean_text(record.content))
            ]

        raw_paragraphs = record.content.split("\n\n")
        paragraphs = [self.clean_text(para) for para in raw_paragraphs if para.strip()]

        chunks: list[BaseRecord] = []
        current_chunk_texts = []
        current_length = 0
        chunk_index = 0

        for para in paragraphs:
            para_len = len(para)
            if current_length + para_len > self.chunk_size and current_chunk_texts:
                chunk_content = "\n\n".join(current_chunk_texts)
                chunks.append(
                    self._create_chunk_record(record, chunk_index, chunk_content)
                )
                chunk_index += 1
                current_chunk_texts = []
                current_length = 0

            current_chunk_texts.append(para)
            current_length += para_len + 2

        if current_chunk_texts:
            chunk_content = "\n\n".join(current_chunk_texts)
            chunks.append(self._create_chunk_record(record, chunk_index, chunk_content))

        return chunks


class RecursiveCharacterChunking(ChunkingStrategy):
    """递归字符分块策略"""

    def __init__(
        self,
        chunk_size: int = 1000,
        overlap: int = 100,
        separators: list[str] | None = None,
    ):
        """
        初始化递归字符分块策略。

        参数:
            chunk_size: 单个分块的最大字符长度限制，默认 1000。
            overlap: 相邻分块之间的重叠字符长度，默认 100。
            separators: 用于切分文本的候选分隔符列表，按优先级从高到低尝试，
                默认包含段落、句子和常见标点。
        """
        if overlap >= chunk_size:
            raise ValueError(f"重叠长度 ({overlap}) 必须小于分块大小 ({chunk_size})")
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.separators = separators or [
            "\n\n",
            "\n",
            "。",
            "！",
            "？",
            "；",
            "，",
            " ",
            "",
        ]

    def _split_text(self, text: str, separators: list[str]) -> list[str]:
        """核心递归切分逻辑。尝试使用给定的分隔符列表按优先级切分文本"""
        final_chunks = []
        separator = separators[-1]
        new_separators = []

        for i, _s in enumerate(separators):
            if _s == "":
                separator = _s
                break
            if _s in text:
                separator = _s
                new_separators = separators[i + 1 :]
                break

        if separator:
            splits = [s for s in text.split(separator) if s]
        else:
            splits = list(text)

        good_splits = []
        for s in splits:
            if len(s) < self.chunk_size:
                good_splits.append(s)
            else:
                if good_splits:
                    merged_chunks = self._merge_splits(good_splits, separator)
                    final_chunks.extend(merged_chunks)
                    good_splits = []
                if new_separators:
                    final_chunks.extend(self._split_text(s, new_separators))
                else:
                    for i in range(0, len(s), self.chunk_size):
                        final_chunks.append(s[i : i + self.chunk_size])

        if good_splits:
            merged_chunks = self._merge_splits(good_splits, separator)
            final_chunks.extend(merged_chunks)

        return final_chunks

    def _merge_splits(self, splits: list[str], separator: str) -> list[str]:
        """将零散的切片合并为符合 chunk_size 的块，并处理 Overlap"""
        chunks = []
        current_chunk = []
        current_length = 0

        for split in splits:
            split_len = len(split)
            sep_len = len(separator) if current_chunk else 0

            if current_length + sep_len + split_len > self.chunk_size and current_chunk:
                chunk_str = separator.join(current_chunk)
                chunks.append(chunk_str)

                while current_length > self.overlap or (
                    current_length + sep_len + split_len > self.chunk_size
                    and len(current_chunk) > 0
                ):
                    popped = current_chunk.pop(0)
                    current_length -= len(popped) + (
                        len(separator) if current_chunk else 0
                    )
                sep_len = len(separator) if current_chunk else 0

            current_chunk.append(split)
            current_length += sep_len + split_len

        if current_chunk:
            chunk_str = separator.join(current_chunk)
            chunks.append(chunk_str)

        return chunks

    def chunk(self, record: BaseRecord) -> list[BaseRecord]:
        """使用递归字符切分方式，将文本切分为带有重叠部分的分块记录"""
        content = record.content.strip()

        if len(content) <= self.chunk_size:
            return [self._create_chunk_record(record, 0, content)]

        text_chunks = self._split_text(content, self.separators)

        chunks: list[BaseRecord] = []
        for i, text_chunk in enumerate(text_chunks):
            clean_chunk = text_chunk.strip()
            if clean_chunk:
                chunks.append(self._create_chunk_record(record, i, clean_chunk))

        return chunks


class RowChunking(ChunkingStrategy):
    """
    行数据分块策略 (专为 CSV/表格设计)
    核心特性：自动识别表头，并将其附加到每一个被切分的 Chunk 首部，防止上下文丢失。
    """

    def __init__(self, rows_per_chunk: int = 50):
        """
        初始化表格行数据分块策略。

        参数:
            rows_per_chunk: 每个分块包含的数据行数（不含表头），默认 50。
        """
        self.rows_per_chunk = rows_per_chunk

    def chunk(self, record: BaseRecord) -> list[BaseRecord]:
        """将表格行数据按指定行数切分为块，每个块都带有相同的表头首部"""
        lines = record.content.splitlines()
        lines = [line for line in lines if line.strip()]

        if not lines:
            return []

        header = lines[0]
        data_lines = lines[1:]

        if not data_lines:
            return [self._create_chunk_record(record, 0, header)]

        chunks: list[BaseRecord] = []
        chunk_index = 0

        for i in range(0, len(data_lines), self.rows_per_chunk):
            chunk_lines = [header, *data_lines[i : i + self.rows_per_chunk]]
            chunk_content = "\n".join(chunk_lines)
            chunks.append(self._create_chunk_record(record, chunk_index, chunk_content))
            chunk_index += 1

        return chunks


class DeduplicationProcessor:
    """
    入库批处理去重器 (Intra-batch Deduplication)。
    在 Chunk 存入数据库前，通过对比向量相似度，拦截高度重复的内容（如群聊复读机内容）。
    """

    def __init__(self, threshold: float = 0.98):
        """
        初始化入库批处理去重处理器。

        参数:
            threshold: 余弦相似度重复阈值，超过该阈值的块将被判定为重复并过滤，
                默认 0.98。
        """
        self.threshold = threshold

    async def process(self, records: list[BaseRecord]) -> list[BaseRecord]:
        """对输入记录列表进行批处理内去重，过滤相似度达到或超过阈值的重复记录"""
        if not records or len(records) <= 1:
            return records

        kept_records: list[BaseRecord] = []
        dropped_count = 0

        for record in records:
            if not record.embedding:
                kept_records.append(record)
                continue

            is_duplicate = False
            for kept in kept_records:
                if not kept.embedding:
                    continue
                sim = cosine_similarity(record.embedding, kept.embedding)
                if sim >= self.threshold:
                    is_duplicate = True
                    dropped_count += 1
                    break

            if not is_duplicate:
                kept_records.append(record)

        if dropped_count > 0:
            logger.debug(
                f"🧹 [入库管线] 触发批处理去重，已拦截 {dropped_count} "
                f"个高度重复的 Chunk (阈值: {self.threshold})"
            )

        return kept_records


class BaseBatchNode(ABC):
    """批处理节点基类：一次性接收并处理全部记录"""

    @abstractmethod
    async def process_batch(self, records: list[BaseRecord]) -> list[BaseRecord]:
        """批量处理 BaseRecord 记录列表"""
        ...


class BaseMapNode(ABC):
    """单映射节点基类：接收单条记录，引擎负责并发调度，返回None代表丢弃该数据"""

    @abstractmethod
    async def process_one(
        self, record: BaseRecord
    ) -> BaseRecord | list[BaseRecord] | None:
        """处理单条 BaseRecord 记录，可返回修改后的记录、拆分后的多条记录，或 None（表示过滤该记录）"""  # noqa: E501
        ...


class DynamicChunkingNode(BaseBatchNode):
    """智能路由切块节点。根据记录的扩展名动态选择切块策略。"""

    def __init__(
        self,
        default_strategy: ChunkingStrategy,
        custom_strategies: dict[str, ChunkingStrategy] | None = None,
    ):
        """
        初始化智能路由切块节点。

        参数:
            default_strategy: 默认的切块策略。
            custom_strategies: 针对特定文件后缀的自定义切块策略映射表，
                默认 CSV 文件使用 RowChunking。
        """
        self.default_strategy = default_strategy
        self.strategies = custom_strategies or {".csv": RowChunking(rows_per_chunk=30)}

    async def process_batch(self, records: list[BaseRecord]) -> list[BaseRecord]:
        """根据记录的元数据扩展名动态匹配并执行切块策略"""
        chunks = []
        for record in records:
            ext = record.metadata.get("extension", "")
            strategy = self.strategies.get(ext, self.default_strategy)
            chunks.extend(strategy.chunk(record))
        return chunks


class BaseEmbeddingBatchNode(BaseBatchNode):
    """批量向量化抽象基类：提取文本、分批请求 API 并将结果 write 回的公共逻辑"""

    def __init__(self, embedder, batch_size: int = 80):
        """
        初始化批量向量化抽象基类。

        参数:
            embedder: 向量嵌入模型/函数，用于将文本生成向量。
            batch_size: 向量化请求的单批次大小限制，默认 80。
        """
        self.embedder = embedder
        self.batch_size = batch_size

    @abstractmethod
    def _filter_target_records(self, records: list[BaseRecord]) -> list[BaseRecord]:
        """由子类实现：筛选出本次需要进行向量化的目标记录"""
        pass

    async def process_batch(self, records: list[BaseRecord]) -> list[BaseRecord]:
        """批量将文本记录提取并请求向量化接口，写回向量数据"""
        if not self.embedder or not records:
            return records

        target_records = self._filter_target_records(records)
        if not target_records:
            return records

        texts = [r.content for r in target_records]
        try:
            vecs = []
            for i in range(0, len(texts), self.batch_size):
                batch_texts = texts[i : i + self.batch_size]
                batch_vecs = await self.embedder(batch_texts, task="document")
                vecs.extend(batch_vecs)

            for i, r in enumerate(target_records):
                if vecs and i < len(vecs) and vecs[i]:
                    r.embedding = vecs[i]
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] 批量向量化失败: {e}")

        return records


class EmbeddingNode(BaseEmbeddingBatchNode):
    """并发向量化初次构建节点，只对有实际内容的记录进行向量化。"""

    def _filter_target_records(self, records: list[BaseRecord]) -> list[BaseRecord]:
        """筛选出非空内容的记录进行向量化"""
        return [r for r in records if r.content.strip()]


class DedupNode(BaseBatchNode):
    """批次内查重节点。在流水线中作为去重节点使用。"""

    def __init__(self, threshold: float):
        """
        初始化批次内查重节点。

        参数:
            threshold: 余弦相似度重复阈值，超过该阈值的块将被判定为重复并过滤。
        """
        self.processor = DeduplicationProcessor(threshold=threshold)

    async def process_batch(self, records: list[BaseRecord]) -> list[BaseRecord]:
        """调用去重处理器过滤本批次中的高度重复记录"""
        return await self.processor.process(records)


class StorageCommitNode(BaseBatchNode):
    """持久化事务提交节点 (Reduce)。统一收集意图并执行并发数据库 I/O。"""

    def __init__(self, storage):
        """
        初始化持久化事务提交节点。

        参数:
            storage: 存储后端，负责将记录存入或删除。
        """
        self.storage = storage

    async def process_batch(self, records: list[BaseRecord]) -> list[BaseRecord]:
        """根据记录的操作类型（保存、更新或删除）分类，并批量提交至存储后端"""
        if not records:
            return records

        to_delete = set()
        to_update = []
        to_insert = []

        for record in records:
            if record.action == "delete":
                to_delete.add(record.id)
            elif record.action == "update":
                to_update.append(record)
            elif record.action == "insert":
                to_insert.append(record)

        if to_delete:
            await self.storage.delete(record_ids=list(to_delete))

        if to_update:
            await asyncio.gather(*[self.storage.update(r) for r in to_update])

        if to_insert:
            await self.storage.save(to_insert)

        logger.debug(
            "💾 RAG 事务提交完成：插入 "
            f"{len(to_insert)} 条, 更新 {len(to_update)} 条, "
            f"删除 {len(to_delete)} 条。"
        )
        return to_insert + to_update


class IndexPipeline:
    """统一入库流水线 (Map-Reduce 范式并发调度引擎)"""

    def __init__(
        self,
        nodes: list[BaseBatchNode | BaseMapNode] | None = None,
        max_workers: int = 5,
    ):
        """
        初始化统一入库流水线。

        参数:
            nodes: 管道节点列表，按顺序执行数据处理，默认 None。
            max_workers: 最大并发工作协程数，用于 Map 节点的并发调度，默认 5。
        """
        self.nodes = nodes or []
        self.max_workers = max_workers

    def add_node(self, node: BaseBatchNode | BaseMapNode):
        """向处理流水线中追加一个节点"""
        self.nodes.append(node)

    async def run(self, records: list[BaseRecord]) -> list[BaseRecord]:
        """并发调度处理引擎，运行并执行流水线中的所有处理节点，返回处理后的记录"""
        if not records:
            return []

        current_records = records
        for node in self.nodes:
            if not current_records:
                break

            if isinstance(node, BaseBatchNode):
                current_records = await node.process_batch(current_records)
            elif isinstance(node, BaseMapNode):
                sem = asyncio.Semaphore(self.max_workers)
                map_node: BaseMapNode = node

                async def _process_with_sem(r: BaseRecord):
                    async with sem:
                        return await map_node.process_one(r)

                tasks = [_process_with_sem(r) for r in current_records]
                results = await asyncio.gather(*tasks)

                next_records = []
                for r in results:
                    if isinstance(r, list):
                        next_records.extend(r)
                    elif r is not None:
                        next_records.append(r)
                current_records = next_records
            else:
                raise ValueError(f"未知的管道节点类型: {type(node)}")

        return current_records


__all__ = [
    "BaseBatchNode",
    "BaseMapNode",
    "ChunkingStrategy",
    "DedupNode",
    "DeduplicationProcessor",
    "DocumentChunking",
    "DynamicChunkingNode",
    "EmbeddingNode",
    "IndexPipeline",
    "RecursiveCharacterChunking",
    "RowChunking",
    "StorageCommitNode",
]
