from typing import Any

from zhenxun.services.ai.utils.scope import normalize_scope_path

from .backends import (
    StorageBackend,
)
from .configs import RAGConfig
from .ingestion import (
    IndexPipeline,
)
from .models import (
    BaseRecord,
    QueryRequest,
    SearchResult,
)
from .retrieval import (
    BaseRetriever,
)
from .utils import normalize_query_text


class ScopedRAGClient:
    """
    RAG 基础设施门面。
    封装了存储后端、检索器和写入管线，将所有操作透明地限定在指定的作用域前缀下。
    """

    def __init__(
        self,
        storage: StorageBackend,
        retriever: BaseRetriever,
        pipeline: IndexPipeline,
        scopes: str | list[str] = "/",
        config: RAGConfig | None = None,
    ):
        """
        初始化 ScopedRAGClient 实例。

        参数:
            storage: 底层向量/文档存储后端。
            retriever: 数据召回检索器。
            pipeline: 数据入库和索引分块处理管线。
            scopes: 数据隔离作用域，支持单作用域前缀或多作用域前缀列表。
            config: 全局的 RAG 配置项。
        """
        self.storage = storage
        self.retriever = retriever
        self.pipeline = pipeline
        self.config = config or RAGConfig()
        self._background_tasks = set()
        if isinstance(scopes, str):
            self.scopes = [normalize_scope_path(scopes)]
        else:
            self.scopes = [normalize_scope_path(s) for s in scopes]

        self.scope_prefix = self.scopes[0] if self.scopes else "/"

    async def ingest(self, records: list[BaseRecord]) -> int:
        """
        通过 RAG Ingestion Pipeline 处理并入库数据。

        参数:
            records: 待导入的数据记录列表。

        返回:
            int: 成功导入并存入的记录数量。
        """
        for r in records:
            if "scope" not in r.metadata:
                r.metadata["scope"] = self.scope_prefix

        res = await self.pipeline.run(records)
        return len(res)

    async def search(
        self,
        query: Any,
        limit: int = 10,
        scopes: str | list[str] | None = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """
        多作用域联合切片视图检索 (Union Search)。
        并发向多个独立的作用域发起检索，并对结果进行合并、去重和重排。

        参数:
            query: 检索的查询对象，可以是文本字符串、向量或高级查询对象。
            limit: 最大返回结果条数。
            scopes: (可选) 自定义检索的作用域。若不提供，则使用客户端初始化时设定的 scopes。
            **kwargs: 传递给底层检索器的其他关键字参数。

        返回:
            list[SearchResult]: 检索到的相似度排序后的结果列表。
        """  # noqa: E501
        target_scopes = self.scopes
        if scopes is not None:
            target_scopes = (
                [normalize_scope_path(scopes)]
                if isinstance(scopes, str)
                else [normalize_scope_path(s) for s in scopes]
            )

        if not target_scopes:
            return []

        text_query = normalize_query_text(query)
        metadata_filters = kwargs.pop("metadata_filters", None)

        request = QueryRequest(
            text=text_query,
            limit=limit,
            scopes=target_scopes,
            metadata_filters=metadata_filters,
            extra=kwargs,
        )

        return await self.retriever.retrieve(request)

    async def update(self, record: BaseRecord) -> None:
        """
        更新指定的已有数据记录。
        会自动强行附加当前客户端的单作用域前缀 (scope_prefix)。

        参数:
            record: 待更新的完整数据记录对象。
        """
        record.metadata["scope"] = self.scope_prefix
        await self.storage.update(record)

    async def delete(self, record_ids: list[str] | None = None, **kwargs: Any) -> int:
        """
        删除指定的已有数据记录，操作限定在当前客户端的单作用域前缀下。

        参数:
            record_ids: (可选) 待删除的记录 ID 列表。
            **kwargs: 传递给底层存储后端的其他过滤或删除参数。

        返回:
            int: 成功删除的记录条数。
        """
        kwargs["scope_prefix"] = self.scope_prefix
        return await self.storage.delete(record_ids=record_ids, **kwargs)
