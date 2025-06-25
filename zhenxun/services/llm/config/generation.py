"""
LLM 生成配置相关类和函数
"""

from typing import Any

from pydantic import BaseModel, Field

from zhenxun.services.log import logger

from ..types.enums import ResponseFormat
from ..types.exceptions import LLMErrorCode, LLMException


class ModelConfigOverride(BaseModel):
    """模型配置覆盖参数"""

    temperature: float | None = Field(
        default=None, ge=0.0, le=2.0, description="生成温度"
    )
    max_tokens: int | None = Field(default=None, gt=0, description="最大输出token数")
    top_p: float | None = Field(default=None, ge=0.0, le=1.0, description="核采样参数")
    top_k: int | None = Field(default=None, gt=0, description="Top-K采样参数")
    frequency_penalty: float | None = Field(
        default=None, ge=-2.0, le=2.0, description="频率惩罚"
    )
    presence_penalty: float | None = Field(
        default=None, ge=-2.0, le=2.0, description="存在惩罚"
    )
    repetition_penalty: float | None = Field(
        default=None, ge=0.0, le=2.0, description="重复惩罚"
    )

    stop: list[str] | str | None = Field(default=None, description="停止序列")

    response_format: ResponseFormat | dict[str, Any] | None = Field(
        default=None, description="期望的响应格式"
    )
    response_mime_type: str | None = Field(
        default=None, description="响应MIME类型（Gemini专用）"
    )
    response_schema: dict[str, Any] | None = Field(
        default=None, description="JSON响应模式"
    )
    thinking_budget: float | None = Field(
        default=None, ge=0.0, le=1.0, description="思考预算"
    )
    safety_settings: dict[str, str] | None = Field(default=None, description="安全设置")
    response_modalities: list[str] | None = Field(
        default=None, description="响应模态类型"
    )

    enable_code_execution: bool | None = Field(
        default=None, description="是否启用代码执行"
    )
    enable_grounding: bool | None = Field(
        default=None, description="是否启用信息来源关联"
    )
    enable_caching: bool | None = Field(default=None, description="是否启用响应缓存")

    custom_params: dict[str, Any] | None = Field(default=None, description="自定义参数")

    def to_dict(self) -> dict[str, Any]:
        """转换为字典，排除None值"""
        result = {}
        model_data = getattr(self, "model_dump", lambda: {})()
        if not model_data:
            model_data = {}
            for field_name, _ in self.__class__.__dict__.get(
                "model_fields", {}
            ).items():
                value = getattr(self, field_name, None)
                if value is not None:
                    model_data[field_name] = value
        for key, value in model_data.items():
            if value is not None:
                if key == "custom_params" and isinstance(value, dict):
                    result.update(value)
                else:
                    result[key] = value
        return result

    def merge_with_base_config(
        self,
        base_temperature: float | None = None,
        base_max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """与基础配置合并，覆盖参数优先"""
        merged = {}

        if base_temperature is not None:
            merged["temperature"] = base_temperature
        if base_max_tokens is not None:
            merged["max_tokens"] = base_max_tokens

        override_dict = self.to_dict()
        merged.update(override_dict)

        return merged


class LLMGenerationConfig(ModelConfigOverride):
    """LLM 生成配置，继承模型配置覆盖参数"""

    def to_api_params(self, api_type: str, model_name: str) -> dict[str, Any]:
        """转换为API参数，支持不同API类型的参数名映射"""
        _ = model_name
        params = {}

        if self.temperature is not None:
            params["temperature"] = self.temperature

        if self.max_tokens is not None:
            if api_type == "gemini":
                params["maxOutputTokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens

        if api_type == "gemini":
            if self.top_k is not None:
                params["topK"] = self.top_k
            if self.top_p is not None:
                params["topP"] = self.top_p
        else:
            if self.top_k is not None:
                params["top_k"] = self.top_k
            if self.top_p is not None:
                params["top_p"] = self.top_p

        if api_type in ["openai", "deepseek", "zhipu", "general_openai_compat"]:
            if self.frequency_penalty is not None:
                params["frequency_penalty"] = self.frequency_penalty
            if self.presence_penalty is not None:
                params["presence_penalty"] = self.presence_penalty

            if self.repetition_penalty is not None:
                if api_type == "openai":
                    logger.warning("OpenAI官方API不支持repetition_penalty参数，已忽略")
                else:
                    params["repetition_penalty"] = self.repetition_penalty

        if self.response_format is not None:
            if isinstance(self.response_format, dict):
                if api_type in ["openai", "zhipu", "deepseek", "general_openai_compat"]:
                    params["response_format"] = self.response_format
                    logger.debug(
                        f"为 {api_type} 使用自定义 response_format: "
                        f"{self.response_format}"
                    )
            elif self.response_format == ResponseFormat.JSON:
                if api_type in ["openai", "zhipu", "deepseek", "general_openai_compat"]:
                    params["response_format"] = {"type": "json_object"}
                    logger.debug(f"为 {api_type} 启用 JSON 对象输出模式")
                elif api_type == "gemini":
                    params["responseMimeType"] = "application/json"
                    if self.response_schema:
                        params["responseSchema"] = self.response_schema
                    logger.debug(f"为 {api_type} 启用 JSON MIME 类型输出模式")

        if api_type == "gemini":
            if (
                self.response_format != ResponseFormat.JSON
                and self.response_mime_type is not None
            ):
                params["responseMimeType"] = self.response_mime_type
                logger.debug(
                    f"使用显式设置的 responseMimeType: {self.response_mime_type}"
                )

            if self.response_schema is not None and "responseSchema" not in params:
                params["responseSchema"] = self.response_schema
            if self.thinking_budget is not None:
                params["thinkingBudget"] = self.thinking_budget
            if self.safety_settings is not None:
                params["safetySettings"] = self.safety_settings
            if self.response_modalities is not None:
                params["responseModalities"] = self.response_modalities

        if self.custom_params:
            custom_mapped = apply_api_specific_mappings(self.custom_params, api_type)
            params.update(custom_mapped)

        logger.debug(f"为{api_type}转换配置参数: {len(params)}个参数")
        return params


def validate_override_params(
    override_config: dict[str, Any] | LLMGenerationConfig | None,
) -> LLMGenerationConfig:
    """验证和标准化覆盖参数"""
    if override_config is None:
        return LLMGenerationConfig()

    if isinstance(override_config, dict):
        try:
            filtered_config = {
                k: v for k, v in override_config.items() if v is not None
            }
            return LLMGenerationConfig(**filtered_config)
        except Exception as e:
            logger.warning(f"覆盖配置参数验证失败: {e}")
            raise LLMException(
                f"无效的覆盖配置参数: {e}",
                code=LLMErrorCode.CONFIGURATION_ERROR,
                cause=e,
            )

    return override_config


def apply_api_specific_mappings(
    params: dict[str, Any], api_type: str
) -> dict[str, Any]:
    """应用API特定的参数映射"""
    mapped_params = params.copy()

    if api_type == "gemini":
        if "max_tokens" in mapped_params:
            mapped_params["maxOutputTokens"] = mapped_params.pop("max_tokens")
        if "top_k" in mapped_params:
            mapped_params["topK"] = mapped_params.pop("top_k")
        if "top_p" in mapped_params:
            mapped_params["topP"] = mapped_params.pop("top_p")

        unsupported = ["frequency_penalty", "presence_penalty", "repetition_penalty"]
        for param in unsupported:
            if param in mapped_params:
                logger.warning(f"Gemini 原生API不支持参数 '{param}'，已忽略")
                mapped_params.pop(param)

    elif api_type in ["openai", "deepseek", "zhipu", "general_openai_compat"]:
        if "repetition_penalty" in mapped_params and api_type == "openai":
            logger.warning("OpenAI官方API不支持repetition_penalty参数，已忽略")
            mapped_params.pop("repetition_penalty")

        if "stop" in mapped_params:
            stop_value = mapped_params["stop"]
            if isinstance(stop_value, str):
                mapped_params["stop"] = [stop_value]

    return mapped_params


def create_generation_config_from_kwargs(**kwargs) -> LLMGenerationConfig:
    """从关键字参数创建生成配置"""
    model_fields = getattr(LLMGenerationConfig, "model_fields", {})
    known_fields = set(model_fields.keys())
    known_params = {}
    custom_params = {}

    for key, value in kwargs.items():
        if key in known_fields:
            known_params[key] = value
        else:
            custom_params[key] = value

    if custom_params:
        known_params["custom_params"] = custom_params

    return LLMGenerationConfig(**known_params)
