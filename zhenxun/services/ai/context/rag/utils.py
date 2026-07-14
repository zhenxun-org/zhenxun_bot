from typing import Any

import numpy as np

from zhenxun.services.ai.context.rag.models import BaseRecord, SearchResult
from zhenxun.services.ai.utils.logger import log_rag as logger


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """使用 numpy 计算两组向量的余弦相似度"""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    v1, v2 = np.array(vec1), np.array(vec2)
    norm1, norm2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(np.dot(v1, v2) / (norm1 * norm2))


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


def normalize_query_text(query: Any) -> str:
    """辅助函数：提取各种输入形式（如字符串、平台Message对象）的纯文本用于检索"""
    if isinstance(query, str):
        return query
    if hasattr(query, "extract_plain_text"):
        return query.extract_plain_text()
    return str(query) if query is not None else ""


class InMemoryScorer:
    """提供内存级别的纯 Python 向量打分与 BM25 稀疏打分工具类"""

    @staticmethod
    def calculate_sparse_scores(
        query: str, candidate_records: list[BaseRecord]
    ) -> list[SearchResult]:
        import jieba

        tokens = set(jieba.lcut_for_search(query.lower()))
        if not tokens:
            return [SearchResult(record=r, score=0.1) for r in candidate_records]

        results = []
        for r in candidate_records:
            content_lower = r.content.lower()
            matched_count = sum(1 for t in tokens if t in content_lower)
            score = matched_count / len(tokens) if tokens else 0.1
            results.append(SearchResult(record=r, score=score))
        return results

    @staticmethod
    def calculate_dense_scores(
        query_embedding: list[float], candidate_records: list[BaseRecord]
    ) -> list[SearchResult]:
        if not candidate_records or not query_embedding:
            return []

        q_vec = normalize_vector(query_embedding)
        vec_records = [r for r in candidate_records if r.embedding]
        missing_records = [r for r in candidate_records if not r.embedding]

        results = []
        if vec_records:
            try:
                raw_mat = np.array([r.embedding for r in vec_records], dtype=np.float32)
                norm_mat = normalize_matrix(raw_mat)
                scores = norm_mat @ q_vec
                for r, score in zip(vec_records, scores):
                    results.append(SearchResult(record=r, score=float(score)))
            except ValueError as e:
                logger.warning(
                    "⚠️ 维度不匹配，已安全跳过向量检索(降级)。原因: " + str(e)
                )

        for r in missing_records:
            results.append(SearchResult(record=r, score=0.1))

        return results
