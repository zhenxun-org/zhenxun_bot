from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


@dataclass
class UsageInfo:
    """使用信息数据类"""

    prompt_tokens: int = 0
    """请求发送的 Token 消耗数 (含系统提示词与历史记录)"""
    completion_tokens: int = 0
    """模型回复生成的 Token 消耗数"""
    total_tokens: int = 0
    """本次交互总计产生的 Token 数"""
    cost: float = 0.0
    """(可选) 本次交互产生的实际账单估价"""
    prompt_cache_hit_tokens: int = 0
    """被上下文缓存系统命中的 Prompt Token 数 (往往价格更低)"""
    prompt_cache_miss_tokens: int = 0
    """未能命中缓存、实际执行了计算的 Prompt Token 数"""
    reasoning_tokens: int = 0
    """专门用于内部思考/推理链 (CoT) 消耗的 Token 数"""

    @property
    def efficiency_ratio(self) -> float:
        return self.completion_tokens / max(self.prompt_tokens, 1)

    def __add__(self, other: "UsageInfo") -> "UsageInfo":
        """支持 UsageInfo 相加，用于汇聚子智能体的 Token 消耗"""
        if not isinstance(other, UsageInfo):
            return self
        return UsageInfo(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost=self.cost + other.cost,
            prompt_cache_hit_tokens=self.prompt_cache_hit_tokens
            + other.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=self.prompt_cache_miss_tokens
            + other.prompt_cache_miss_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


class LLMCodeExecution(BaseModel):
    """大模型代码执行（沙箱/本地）结果实体"""

    code: str
    """被执行的原始代码"""
    output: str | None = None
    """标准输出 (stdout)"""
    error: str | None = None
    """标准错误 (stderr) 或框架抛出的异常"""
    execution_time: float | None = None
    """代码执行耗时 (秒)"""
    files_generated: list[str] | None = None
    """代码执行过程中生成的工件(Artifacts)文件路径或名称列表"""


class LLMGroundingAttribution(BaseModel):
    """基础事实溯源引用对象 (Grounding Attribution)"""

    title: str | None = None
    """来源网页或文档的标题"""
    uri: str | None = None
    """来源内容的统一资源标识符 (URL)"""
    snippet: str | None = None
    """从来源网页中提取的、支撑当前生成内容的文本片段"""
    confidence_score: float | None = None
    """该引用来源与生成内容之间相关性的置信度分数"""


class LLMGroundingMetadata(BaseModel):
    """检索增强/搜索引擎溯源 (Grounding) 的完整元数据字典，
    用于为大模型返回的信息提供可信背书"""

    web_search_queries: list[str] | None = None
    """模型在执行检索时，实际使用的底层搜索引擎 Query 查询词列表"""
    grounding_attributions: list[LLMGroundingAttribution] | None = None
    """溯源引用的详情列表，用于在 UI 端构建点击跳转链接或角标"""
    search_suggestions: list[dict[str, Any]] | None = None
    """随搜索返回的相关搜索建议 (Search Suggestions)"""
    search_entry_point: str | None = None
    """一段 HTML/CSS 内容，可用于在客户端渲染标准的搜索引擎入口/建议组件"""
    map_widget_token: str | None = None
    """用于渲染 Google Maps 交互式地点小组件 (Places widget) 的
    上下文 Token (针对 googleMaps 工具)"""


class RerankDocument(BaseModel):
    """重排候选文档 (支持纯文本或图文字典)"""

    text: str | None = None
    """被用于重排检索的文本内容"""
    image: str | None = None
    """用于多模态重排的图片内容"""


class RerankResult(BaseModel):
    """重排返回结果"""

    index: int
    """此记录对应于输入时的原始文档数组中的索引位置"""
    relevance_score: float
    """计算出的相关性得分 (越大相关度通常越高)"""
    document: RerankDocument | None = Field(default=None)
    """实际被命中的文档数据"""


__all__ = [
    "LLMCodeExecution",
    "LLMGroundingAttribution",
    "LLMGroundingMetadata",
    "RerankDocument",
    "RerankResult",
    "UsageInfo",
]
