"""
LLM 枚举类型定义
"""

from enum import Enum, auto


class ModelProvider(Enum):
    """模型提供商枚举"""

    OPENAI = "openai"
    GEMINI = "gemini"
    ZHIXPU = "zhipu"
    CUSTOM = "custom"


class ResponseFormat(Enum):
    """响应格式枚举"""

    TEXT = "text"
    JSON = "json"
    MULTIMODAL = "multimodal"


class EmbeddingTaskType(str, Enum):
    """文本嵌入任务类型 (主要用于Gemini)"""

    RETRIEVAL_QUERY = "RETRIEVAL_QUERY"
    RETRIEVAL_DOCUMENT = "RETRIEVAL_DOCUMENT"
    SEMANTIC_SIMILARITY = "SEMANTIC_SIMILARITY"
    CLASSIFICATION = "CLASSIFICATION"
    CLUSTERING = "CLUSTERING"
    QUESTION_ANSWERING = "QUESTION_ANSWERING"
    FACT_VERIFICATION = "FACT_VERIFICATION"


class ToolCategory(Enum):
    """工具分类枚举"""

    FILE_SYSTEM = auto()
    NETWORK = auto()
    SYSTEM_INFO = auto()
    CALCULATION = auto()
    DATA_PROCESSING = auto()
    CUSTOM = auto()


class TaskType(Enum):
    """任务类型枚举"""

    CHAT = "chat"
    CODE = "code"
    SEARCH = "search"
    ANALYSIS = "analysis"
    GENERATION = "generation"
    MULTIMODAL = "multimodal"


class LLMErrorCode(Enum):
    """LLM 服务相关的错误代码枚举"""

    MODEL_INIT_FAILED = 2000
    MODEL_NOT_FOUND = 2001
    API_REQUEST_FAILED = 2002
    API_RESPONSE_INVALID = 2003
    API_KEY_INVALID = 2004
    API_QUOTA_EXCEEDED = 2005
    API_TIMEOUT = 2006
    API_RATE_LIMITED = 2007
    NO_AVAILABLE_KEYS = 2008
    UNKNOWN_API_TYPE = 2009
    CONFIGURATION_ERROR = 2010
    RESPONSE_PARSE_ERROR = 2011
    CONTEXT_LENGTH_EXCEEDED = 2012
    CONTENT_FILTERED = 2013
    USER_LOCATION_NOT_SUPPORTED = 2014
    GENERATION_FAILED = 2015
    EMBEDDING_FAILED = 2016
