import os
from typing import ClassVar, Protocol, runtime_checkable
import uuid

import numpy as np
from tortoise import fields

from zhenxun.services.ai.context.rag.models import (
    BaseRecord,
    QueryRequest,
    SearchResult,
)
from zhenxun.services.ai.context.rag.retrieval import FilterEvaluator
from zhenxun.services.ai.utils.logger import log_rag as logger
from zhenxun.services.ai.utils.scope import ScopeSelector
from zhenxun.services.db_context import Model


@runtime_checkable
class StorageBackend(Protocol):
    """纯粹的向量存储后端协议"""

    async def save(self, records: list[BaseRecord]) -> None:
        """保存或更新数据块"""
        ...

    async def search(
        self, query: QueryRequest, scopes: list[str] | None = None
    ) -> list[SearchResult]:
        """按向量和前缀检索数据块"""
        ...

    async def update(self, record: BaseRecord) -> None:
        """更新已有数据块"""
        ...

    async def delete(
        self, record_ids: list[str] | None = None, scope_prefix: str | None = None
    ) -> int:
        """删除数据块"""
        ...

    async def clear_by_query(self, query: ScopeSelector) -> int:
        """根据统一领域查询对象清理数据块（在各实现中回退到 delete）"""
        ...

    async def get_all(self, scope_prefix: str | None = None) -> list[BaseRecord]:
        """获取作用域下所有记录（用于容量控制）"""
        ...


def normalize_vector(vec: list[float] | np.ndarray) -> np.ndarray:
    """将一维向量转化为 float32 数组并进行 L2 归一化"""
    v = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(v)
    if norm == 0:
        return v
    return v / norm


def normalize_matrix(mat: np.ndarray) -> np.ndarray:
    """将二维矩阵的每一行进行 L2 归一化"""
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class DictStorageBackend(StorageBackend):
    """基于内存字典的轻量级纯净 RAG 存储实现"""

    _shared_records: ClassVar[dict[str, BaseRecord]] = {}
    _shared_vectors: ClassVar[dict[str, np.ndarray]] = {}

    def __init__(self):
        self._records = self._shared_records
        self._vectors = self._shared_vectors

    async def save(self, records: list[BaseRecord]) -> None:
        for r in records:
            self._records[r.id] = r
            if r.embedding:
                self._vectors[r.id] = normalize_vector(r.embedding)
            else:
                self._vectors.pop(r.id, None)

    async def search(
        self, query: QueryRequest, scopes: list[str] | None = None
    ) -> list[SearchResult]:
        candidate_ids = []
        for record in self._records.values():
            if scopes is not None:
                if record.metadata.get("scope", "/") not in scopes:
                    continue
            if not FilterEvaluator.evaluate(record.metadata, query.metadata_filters):
                continue
            if not query.embedding and query.text and query.text not in record.content:
                continue
            candidate_ids.append(record.id)

        if not candidate_ids:
            return []

        results = []
        if query.search_type == "sparse":
            import jieba

            tokens = set(jieba.lcut_for_search(query.text.lower()))
            for r_id in candidate_ids:
                record = self._records[r_id]
                content = record.content.lower()
                matched_count = sum(1 for t in tokens if t in content)
                if matched_count > 0:
                    score = matched_count / len(tokens)
                    results.append(SearchResult(record=record, score=score))
        elif query.search_type == "dense" and query.embedding:
            q_vec = normalize_vector(query.embedding)
            valid_ids = [r_id for r_id in candidate_ids if r_id in self._vectors]

            if valid_ids:
                try:
                    mat = np.array([self._vectors[r_id] for r_id in valid_ids])
                    scores = mat @ q_vec
                    for r_id, score in zip(valid_ids, scores):
                        results.append(
                            SearchResult(record=self._records[r_id], score=float(score))
                        )
                except ValueError as e:
                    logger.warning(
                        "⚠️ DictStorage 中缓存的向量维度与当前查询维度不匹配，"
                        f"跳过向量检索。原因: {e}"
                    )

            missing_ids = [r_id for r_id in candidate_ids if r_id not in self._vectors]
            for r_id in missing_ids:
                results.append(SearchResult(record=self._records[r_id], score=0.1))
        else:
            for r_id in candidate_ids:
                results.append(SearchResult(record=self._records[r_id], score=0.1))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[: query.limit]

    async def update(self, record: BaseRecord) -> None:
        if record.id in self._records:
            self._records[record.id] = record
            if record.embedding:
                self._vectors[record.id] = normalize_vector(record.embedding)
            else:
                self._vectors.pop(record.id, None)

    async def delete(
        self, record_ids: list[str] | None = None, scope_prefix: str | None = None
    ) -> int:
        to_delete = []
        for r_id, r in self._records.items():
            if scope_prefix is not None:
                if not r.metadata.get("scope", "/").startswith(scope_prefix):
                    continue
            if record_ids and r_id not in record_ids:
                continue
            to_delete.append(r_id)
        for r_id in to_delete:
            del self._records[r_id]
            self._vectors.pop(r_id, None)
        return len(to_delete)

    async def clear_by_query(self, query: ScopeSelector) -> int:
        return await self.delete(scope_prefix=query.scope_prefix)

    async def get_all(self, scope_prefix: str | None = None) -> list[BaseRecord]:
        res = []
        for r in self._records.values():
            if scope_prefix is not None:
                if r.metadata.get("scope", "/") != scope_prefix:
                    continue
            res.append(r)
        return res


class AbstractVectorRecord(Model):
    id = fields.CharField(pk=True, max_length=64)
    scope = fields.CharField(max_length=255, index=True)
    content = fields.TextField()
    embedding = fields.JSONField(null=True)
    meta_data = fields.JSONField(null=True)

    class Meta:  # type: ignore
        abstract = True


class TortoiseStorageBackend(StorageBackend):
    def __init__(self, model_class: type[AbstractVectorRecord]):
        self.model_class = model_class

    def _to_base_record(self, row: AbstractVectorRecord) -> BaseRecord:
        return BaseRecord(
            id=row.id,
            content=row.content,
            embedding=row.embedding if isinstance(row.embedding, list) else None,
            metadata=row.meta_data if isinstance(row.meta_data, dict) else {},
        )

    async def save(self, records: list[BaseRecord]) -> None:
        for r in records:
            await self.model_class.update_or_create(
                id=r.id,
                defaults={
                    "content": r.content,
                    "scope": r.metadata.get("scope", "/"),
                    "embedding": r.embedding,
                    "meta_data": r.metadata,
                },
            )

    async def search(
        self, query: QueryRequest, scopes: list[str] | None = None
    ) -> list[SearchResult]:
        query_orm = self.model_class.all()
        if scopes is not None:
            query_orm = query_orm.filter(scope__in=scopes)

        if query.search_type == "sparse" and query.text:
            import jieba
            from tortoise.expressions import Q

            tokens = [
                t for t in jieba.lcut_for_search(query.text) if len(t.strip()) > 1
            ] or [query.text]
            q_expr = Q()
            for token in tokens:
                q_expr |= Q(content__icontains=token)
            query_orm = query_orm.filter(q_expr)
        elif query.search_type == "dense" and not query.embedding and query.text:
            query_orm = query_orm.filter(content__icontains=query.text)

        rows = await query_orm

        valid_rows = []
        for row in rows:
            row_meta = row.meta_data if isinstance(row.meta_data, dict) else {}
            if not FilterEvaluator.evaluate(row_meta, query.metadata_filters):
                continue
            valid_rows.append(row)

        if not valid_rows:
            return []

        results = []
        if query.search_type == "sparse":
            import jieba

            tokens = set(jieba.lcut_for_search(query.text.lower()))
            for row in valid_rows:
                content = row.content.lower()
                matched_count = sum(1 for t in tokens if t in content)
                score = matched_count / len(tokens) if tokens else 0.1
                results.append(
                    SearchResult(record=self._to_base_record(row), score=score)
                )
        elif query.search_type == "dense" and query.embedding:
            q_vec = normalize_vector(query.embedding)
            vec_rows = []
            missing_rows = []

            for row in valid_rows:
                if isinstance(row.embedding, list):
                    vec_rows.append(row)
                else:
                    missing_rows.append(row)

            if vec_rows:
                try:
                    raw_mat = np.array(
                        [r.embedding for r in vec_rows], dtype=np.float32
                    )
                    norm_mat = normalize_matrix(raw_mat)
                    scores = norm_mat @ q_vec

                    for row, score in zip(vec_rows, scores):
                        results.append(
                            SearchResult(
                                record=self._to_base_record(row), score=float(score)
                            )
                        )
                except ValueError as e:
                    logger.warning(
                        "⚠️ 数据库中缓存的向量维度与当前模型查询维度不匹配，"
                        f"已安全跳过向量检索(降级为稀疏匹配)。原因: {e}"
                    )

            for row in missing_rows:
                results.append(
                    SearchResult(record=self._to_base_record(row), score=0.1)
                )
        else:
            for row in valid_rows:
                results.append(
                    SearchResult(record=self._to_base_record(row), score=0.1)
                )

        results.sort(key=lambda x: x.score, reverse=True)
        return results[: query.limit]

    async def update(self, record: BaseRecord) -> None:
        await self.model_class.filter(id=record.id).update(
            content=record.content,
            scope=record.metadata.get("scope", "/"),
            embedding=record.embedding,
            meta_data=record.metadata,
        )

    async def delete(
        self, record_ids: list[str] | None = None, scope_prefix: str | None = None
    ) -> int:
        query = self.model_class.all()
        if scope_prefix is not None:
            query = query.filter(scope__startswith=scope_prefix)
        if record_ids is not None:
            if not record_ids:
                return 0
            query = query.filter(id__in=record_ids)

        return await query.delete()

    async def clear_by_query(self, query: ScopeSelector) -> int:
        return await self.delete(scope_prefix=query.scope_prefix)

    async def get_all(self, scope_prefix: str | None = None) -> list[BaseRecord]:
        query = self.model_class.all()
        if scope_prefix is not None:
            query = query.filter(scope=scope_prefix)
        rows = await query
        return [self._to_base_record(row) for row in rows]


class QdrantStorageBackend(StorageBackend):
    """Qdrant 向量数据库可选存储后端"""

    def __init__(
        self,
        location: str = ":memory:",
        collection_name: str = "zhenxun_rag",
        **kwargs,
    ):
        try:
            from qdrant_client import AsyncQdrantClient
        except ImportError:
            raise ImportError(
                "缺少 Qdrant 依赖！请执行 `pip install qdrant-client` 安装"
            )

        self.client = AsyncQdrantClient(location=location, **kwargs)
        self.collection_name = collection_name
        self._initialized = False

    async def _ensure_collection(self, dim: int):
        if self._initialized:
            return
        from qdrant_client.models import Distance, VectorParams

        if not await self.client.collection_exists(self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
        self._initialized = True

    async def save(self, records: list[BaseRecord]) -> None:
        if not records:
            return
        dim = len(records[0].embedding) if records[0].embedding else 1536
        await self._ensure_collection(dim)

        from qdrant_client.models import PointStruct

        points = []
        for r in records:
            points.append(
                PointStruct(
                    id=r.id
                    if len(r.id) == 36
                    else str(uuid.uuid5(uuid.NAMESPACE_DNS, r.id)),
                    vector=r.embedding or [],
                    payload={"content": r.content, "metadata": r.metadata},
                )
            )
        await self.client.upsert(collection_name=self.collection_name, points=points)

    async def search(
        self, query: QueryRequest, scopes: list[str] | None = None
    ) -> list[SearchResult]:
        if query.search_type == "dense" and not query.embedding:
            return []
        if query.embedding:
            await self._ensure_collection(len(query.embedding))

        from qdrant_client.models import FieldCondition, Filter, MatchText, MatchValue

        must_conditions = []

        if scopes is not None:
            try:
                from qdrant_client.models import MatchAny

                must_conditions.append(
                    FieldCondition(key="metadata.scope", match=MatchAny(any=scopes))
                )
            except ImportError:
                scope_conditions = [
                    FieldCondition(key="metadata.scope", match=MatchValue(value=s))
                    for s in scopes
                ]
                must_conditions.append(Filter(should=scope_conditions))

        if query.metadata_filters:
            for k, v in query.metadata_filters.items():
                must_conditions.append(
                    FieldCondition(key=f"metadata.{k}", match=MatchValue(value=v))
                )

        if query.search_type == "sparse":
            must_conditions.append(
                FieldCondition(key="content", match=MatchText(text=query.text))
            )

        query_filter = Filter(must=must_conditions) if must_conditions else None

        if query.search_type == "sparse":
            results = await self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=query_filter,
                limit=query.limit,
                with_payload=True,
            )
            return [
                SearchResult(
                    record=BaseRecord(
                        id=str(r.id),
                        content=(r.payload or {}).get("content", ""),
                        metadata=(r.payload or {}).get("metadata", {}),
                    ),
                    score=1.0,
                )
                for r in results[0]
            ]

        results = await self.client.search(  # type: ignore
            collection_name=self.collection_name,
            query_vector=query.embedding,
            limit=query.limit,
            query_filter=query_filter,
        )

        return [
            SearchResult(
                record=BaseRecord(
                    id=str(r.id),
                    content=r.payload.get("content", ""),
                    metadata=r.payload.get("metadata", {}),
                ),
                score=r.score,
            )
            for r in results
        ]

    async def update(self, record: BaseRecord) -> None:
        await self.save([record])

    async def delete(
        self, record_ids: list[str] | None = None, scope_prefix: str | None = None
    ) -> int:
        if not await self.client.collection_exists(self.collection_name):
            return 0
        from qdrant_client.models import FieldCondition, Filter, MatchText

        query_filter = None
        if scope_prefix is not None:
            query_filter = Filter(
                must=[
                    FieldCondition(
                        key="metadata.scope", match=MatchText(text=scope_prefix)
                    )
                ]
            )
        if query_filter:
            await self.client.delete(
                collection_name=self.collection_name, points_selector=query_filter
            )
        return 1

    async def clear_by_query(self, query: ScopeSelector) -> int:
        return await self.delete(scope_prefix=query.scope_prefix)

    async def get_all(self, scope_prefix: str | None = None) -> list[BaseRecord]:
        if not await self.client.collection_exists(self.collection_name):
            return []
        from qdrant_client.models import FieldCondition, Filter, MatchText

        q_filter = None
        if scope_prefix and scope_prefix != "/":
            q_filter = Filter(
                must=[
                    FieldCondition(
                        key="metadata.scope", match=MatchText(text=scope_prefix)
                    )
                ]
            )
        res = await self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=q_filter,
            limit=10000,
            with_payload=True,
        )
        return [
            BaseRecord(
                id=str(r.id),
                content=(r.payload or {}).get("content", ""),
                metadata=(r.payload or {}).get("metadata", {}),
            )
            for r in res[0]
        ]


class LanceDBStorageBackend(StorageBackend):
    """LanceDB 向量数据库可选存储后端"""

    def __init__(
        self, uri: str = "./data/lancedb", table_name: str = "zhenxun_rag", **kwargs
    ):
        try:
            import lancedb
        except ImportError:
            raise ImportError("缺少 LanceDB 依赖！请执行 `pip install lancedb` 安装")

        os.makedirs(
            os.path.dirname(uri) if os.path.dirname(uri) else ".", exist_ok=True
        )
        self.db = lancedb.connect(uri)
        self.table_name = table_name

    async def save(self, records: list[BaseRecord]) -> None:
        if not records:
            return
        data = []
        dim = len(records[0].embedding) if records[0].embedding else 0

        for r in records:
            data.append(
                {
                    "id": r.id,
                    "vector": r.embedding or [0.0] * dim,
                    "content": r.content,
                    "metadata": str(r.metadata),
                }
            )

        if self.table_name not in self.db.table_names():
            self.db.create_table(self.table_name, data=data)
        else:
            self.db.open_table(self.table_name).add(data)

    async def search(
        self, query: QueryRequest, scopes: list[str] | None = None
    ) -> list[SearchResult]:
        if self.table_name not in self.db.table_names():
            return []
        if query.search_type == "dense" and not query.embedding:
            return []

        tbl = self.db.open_table(self.table_name)
        if query.search_type == "sparse":
            try:
                results = (
                    tbl.search(query.text, query_type="fts")
                    .limit(query.limit)
                    .to_list()
                )
            except Exception as e:
                logger.warning(f"LanceDB FTS 检索失败(可能是由于尚未创建FTS索引): {e}")
                return []
        else:
            results = tbl.search(query.embedding).limit(query.limit).to_list()

        import ast

        return [
            SearchResult(
                record=BaseRecord(
                    id=r["id"],
                    content=r["content"],
                    metadata=ast.literal_eval(r["metadata"]) if "metadata" in r else {},
                ),
                score=1.0 - r.get("_distance", 0.0),
            )
            for r in results
        ]

    async def update(self, record: BaseRecord) -> None:
        pass

    async def delete(
        self, record_ids: list[str] | None = None, scope_prefix: str | None = None
    ) -> int:
        return 0

    async def clear_by_query(self, query: ScopeSelector) -> int:
        return await self.delete(scope_prefix=query.scope_prefix)

    async def get_all(self, scope_prefix: str | None = None) -> list[BaseRecord]:
        if self.table_name not in self.db.table_names():
            return []
        tbl = self.db.open_table(self.table_name)
        df = tbl.to_pandas()
        import ast

        res = []
        for _, row in df.iterrows():
            meta = ast.literal_eval(row["metadata"]) if "metadata" in row else {}
            if scope_prefix is not None:
                if meta.get("scope", "/") != scope_prefix:
                    continue
            res.append(BaseRecord(id=row["id"], content=row["content"], metadata=meta))
        return res
