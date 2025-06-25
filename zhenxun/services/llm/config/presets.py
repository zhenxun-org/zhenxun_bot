"""
LLM 预设配置

提供常用的配置预设，特别是针对 Gemini 的高级功能。
"""

from typing import Any

from .generation import LLMGenerationConfig


class CommonOverrides:
    """常用的配置覆盖预设"""

    @staticmethod
    def creative() -> LLMGenerationConfig:
        """创意模式：高温度，鼓励创新"""
        return LLMGenerationConfig(temperature=0.9, top_p=0.95, frequency_penalty=0.1)

    @staticmethod
    def precise() -> LLMGenerationConfig:
        """精确模式：低温度，确定性输出"""
        return LLMGenerationConfig(temperature=0.1, top_p=0.9, frequency_penalty=0.0)

    @staticmethod
    def balanced() -> LLMGenerationConfig:
        """平衡模式：中等温度"""
        return LLMGenerationConfig(temperature=0.5, top_p=0.9, frequency_penalty=0.0)

    @staticmethod
    def concise(max_tokens: int = 100) -> LLMGenerationConfig:
        """简洁模式：限制输出长度"""
        return LLMGenerationConfig(
            temperature=0.3,
            max_tokens=max_tokens,
            stop=["\n\n", "。", "！", "？"],
        )

    @staticmethod
    def detailed(max_tokens: int = 2000) -> LLMGenerationConfig:
        """详细模式：鼓励详细输出"""
        return LLMGenerationConfig(
            temperature=0.7, max_tokens=max_tokens, frequency_penalty=-0.1
        )

    @staticmethod
    def gemini_json() -> LLMGenerationConfig:
        """Gemini JSON模式：强制JSON输出"""
        return LLMGenerationConfig(
            temperature=0.3, response_mime_type="application/json"
        )

    @staticmethod
    def gemini_thinking(budget: float = 0.8) -> LLMGenerationConfig:
        """Gemini 思考模式：使用思考预算"""
        return LLMGenerationConfig(temperature=0.7, thinking_budget=budget)

    @staticmethod
    def gemini_creative() -> LLMGenerationConfig:
        """Gemini 创意模式：高温度创意输出"""
        return LLMGenerationConfig(temperature=0.9, top_p=0.95)

    @staticmethod
    def gemini_structured(schema: dict[str, Any]) -> LLMGenerationConfig:
        """Gemini 结构化输出：自定义JSON模式"""
        return LLMGenerationConfig(
            temperature=0.3,
            response_mime_type="application/json",
            response_schema=schema,
        )

    @staticmethod
    def gemini_safe() -> LLMGenerationConfig:
        """Gemini 安全模式：使用配置的安全设置"""
        from .providers import get_gemini_safety_threshold

        threshold = get_gemini_safety_threshold()
        return LLMGenerationConfig(
            temperature=0.5,
            safety_settings={
                "HARM_CATEGORY_HARASSMENT": threshold,
                "HARM_CATEGORY_HATE_SPEECH": threshold,
                "HARM_CATEGORY_SEXUALLY_EXPLICIT": threshold,
                "HARM_CATEGORY_DANGEROUS_CONTENT": threshold,
            },
        )

    @staticmethod
    def gemini_multimodal() -> LLMGenerationConfig:
        """Gemini 多模态模式：优化多模态处理"""
        return LLMGenerationConfig(temperature=0.6, max_tokens=2048, top_p=0.8)

    @staticmethod
    def gemini_code_execution() -> LLMGenerationConfig:
        """Gemini 代码执行模式：启用代码执行功能"""
        return LLMGenerationConfig(
            temperature=0.3,
            max_tokens=4096,
            enable_code_execution=True,
            custom_params={"code_execution_timeout": 30},
        )

    @staticmethod
    def gemini_grounding() -> LLMGenerationConfig:
        """Gemini 信息来源关联模式：启用Google搜索"""
        return LLMGenerationConfig(
            temperature=0.5,
            max_tokens=4096,
            enable_grounding=True,
            custom_params={
                "grounding_config": {"dynamicRetrievalConfig": {"mode": "MODE_DYNAMIC"}}
            },
        )

    @staticmethod
    def gemini_cached() -> LLMGenerationConfig:
        """Gemini 缓存模式：启用响应缓存"""
        return LLMGenerationConfig(
            temperature=0.3,
            max_tokens=2048,
            enable_caching=True,
        )

    @staticmethod
    def gemini_advanced() -> LLMGenerationConfig:
        """Gemini 高级模式：启用所有高级功能"""
        return LLMGenerationConfig(
            temperature=0.5,
            max_tokens=4096,
            enable_code_execution=True,
            enable_grounding=True,
            enable_caching=True,
            custom_params={
                "code_execution_timeout": 30,
                "grounding_config": {
                    "dynamicRetrievalConfig": {"mode": "MODE_DYNAMIC"}
                },
            },
        )

    @staticmethod
    def gemini_research() -> LLMGenerationConfig:
        """Gemini 研究模式：思考+搜索+结构化输出"""
        return LLMGenerationConfig(
            temperature=0.6,
            max_tokens=4096,
            thinking_budget=0.8,
            enable_grounding=True,
            response_mime_type="application/json",
            custom_params={
                "grounding_config": {"dynamicRetrievalConfig": {"mode": "MODE_DYNAMIC"}}
            },
        )

    @staticmethod
    def gemini_analysis() -> LLMGenerationConfig:
        """Gemini 分析模式：深度思考+详细输出"""
        return LLMGenerationConfig(
            temperature=0.4,
            max_tokens=6000,
            thinking_budget=0.9,
            top_p=0.8,
        )

    @staticmethod
    def gemini_fast_response() -> LLMGenerationConfig:
        """Gemini 快速响应模式：低延迟+简洁输出"""
        return LLMGenerationConfig(
            temperature=0.3,
            max_tokens=512,
            top_p=0.8,
        )
