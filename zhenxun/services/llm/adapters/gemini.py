"""
Gemini API 适配器
"""

from typing import TYPE_CHECKING, Any

from zhenxun.services.log import logger

from ..types.exceptions import LLMErrorCode, LLMException
from ..utils import sanitize_schema_for_llm
from .base import BaseAdapter, RequestData, ResponseData

if TYPE_CHECKING:
    from ..config.generation import LLMGenerationConfig
    from ..service import LLMModel
    from ..types.content import LLMMessage
    from ..types.enums import EmbeddingTaskType
    from ..types.models import LLMToolCall
    from ..types.protocols import ToolExecutable


class GeminiAdapter(BaseAdapter):
    """Gemini API 适配器"""

    @property
    def api_type(self) -> str:
        return "gemini"

    @property
    def supported_api_types(self) -> list[str]:
        return ["gemini"]

    def get_base_headers(self, api_key: str) -> dict[str, str]:
        """获取基础请求头"""
        from zhenxun.utils.user_agent import get_user_agent

        headers = get_user_agent()
        headers.update({"Content-Type": "application/json"})
        headers["x-goog-api-key"] = api_key

        return headers

    async def prepare_advanced_request(
        self,
        model: "LLMModel",
        api_key: str,
        messages: list["LLMMessage"],
        config: "LLMGenerationConfig | None" = None,
        tools: dict[str, "ToolExecutable"] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> RequestData:
        """准备高级请求"""
        effective_config = config if config is not None else model._generation_config

        endpoint = self._get_gemini_endpoint(model, effective_config)
        url = self.get_api_url(model, endpoint)
        headers = self.get_base_headers(api_key)

        gemini_contents: list[dict[str, Any]] = []
        system_instruction_parts: list[dict[str, Any]] | None = None

        for msg in messages:
            current_parts: list[dict[str, Any]] = []
            if msg.role == "system":
                if isinstance(msg.content, str):
                    system_instruction_parts = [{"text": msg.content}]
                elif isinstance(msg.content, list):
                    system_instruction_parts = [
                        await part.convert_for_api_async("gemini")
                        for part in msg.content
                    ]
                continue

            elif msg.role == "user":
                if isinstance(msg.content, str):
                    current_parts.append({"text": msg.content})
                elif isinstance(msg.content, list):
                    for part_obj in msg.content:
                        current_parts.append(
                            await part_obj.convert_for_api_async("gemini")
                        )
                gemini_contents.append({"role": "user", "parts": current_parts})

            elif msg.role == "assistant" or msg.role == "model":
                if isinstance(msg.content, str) and msg.content:
                    current_parts.append({"text": msg.content})
                elif isinstance(msg.content, list):
                    for part_obj in msg.content:
                        current_parts.append(
                            await part_obj.convert_for_api_async("gemini")
                        )

                if msg.tool_calls:
                    import json

                    for call in msg.tool_calls:
                        current_parts.append(
                            {
                                "functionCall": {
                                    "name": call.function.name,
                                    "args": json.loads(call.function.arguments),
                                }
                            }
                        )
                if current_parts:
                    gemini_contents.append({"role": "model", "parts": current_parts})

            elif msg.role == "tool":
                if not msg.name:
                    raise ValueError("Gemini 工具消息必须包含 'name' 字段（函数名）。")

                import json

                try:
                    content_str = (
                        msg.content
                        if isinstance(msg.content, str)
                        else str(msg.content)
                    )
                    tool_result_obj = json.loads(content_str)
                except json.JSONDecodeError:
                    content_str = (
                        msg.content
                        if isinstance(msg.content, str)
                        else str(msg.content)
                    )
                    logger.warning(
                        f"工具 {msg.name} 的结果不是有效的 JSON: {content_str}. "
                        f"包装为原始字符串。"
                    )
                    tool_result_obj = {"raw_output": content_str}

                if isinstance(tool_result_obj, list):
                    logger.debug(
                        f"工具 '{msg.name}' 的返回结果是列表，"
                        f"正在为Gemini API包装为JSON对象。"
                    )
                    final_response_payload = {"result": tool_result_obj}
                elif not isinstance(tool_result_obj, dict):
                    final_response_payload = {"result": tool_result_obj}
                else:
                    final_response_payload = tool_result_obj

                current_parts.append(
                    {
                        "functionResponse": {
                            "name": msg.name,
                            "response": final_response_payload,
                        }
                    }
                )
                gemini_contents.append({"role": "function", "parts": current_parts})

        body: dict[str, Any] = {"contents": gemini_contents}

        if system_instruction_parts:
            body["systemInstruction"] = {"parts": system_instruction_parts}

        all_tools_for_request = []
        if tools:
            import asyncio

            from zhenxun.utils.pydantic_compat import model_dump

            definition_tasks = [
                executable.get_definition() for executable in tools.values()
            ]
            tool_definitions = await asyncio.gather(*definition_tasks)

            function_declarations = []
            for tool_def in tool_definitions:
                tool_def.parameters = sanitize_schema_for_llm(
                    tool_def.parameters, api_type="gemini"
                )
                function_declarations.append(model_dump(tool_def))

            if function_declarations:
                all_tools_for_request.append(
                    {"functionDeclarations": function_declarations}
                )

        if effective_config:
            if getattr(effective_config, "enable_grounding", False):
                has_explicit_gs_tool = any(
                    "googleSearch" in tool_item for tool_item in all_tools_for_request
                )
                if not has_explicit_gs_tool:
                    all_tools_for_request.append({"googleSearch": {}})
                    logger.debug("隐式启用 Google Search 工具进行信息来源关联。")

            if getattr(effective_config, "enable_code_execution", False):
                has_explicit_ce_tool = any(
                    "codeExecution" in tool_item for tool_item in all_tools_for_request
                )
                if not has_explicit_ce_tool:
                    all_tools_for_request.append({"codeExecution": {}})
                    logger.debug("隐式启用代码执行工具。")

        if all_tools_for_request:
            body["tools"] = all_tools_for_request

        final_tool_choice = tool_choice
        if final_tool_choice is None and effective_config:
            final_tool_choice = getattr(effective_config, "tool_choice", None)

        if final_tool_choice:
            if isinstance(final_tool_choice, str):
                mode_upper = final_tool_choice.upper()
                if mode_upper in ["AUTO", "NONE", "ANY"]:
                    body["toolConfig"] = {"functionCallingConfig": {"mode": mode_upper}}
                else:
                    body["toolConfig"] = self._convert_tool_choice_to_gemini(
                        final_tool_choice
                    )
            else:
                body["toolConfig"] = self._convert_tool_choice_to_gemini(
                    final_tool_choice
                )

        final_generation_config = self._build_gemini_generation_config(
            model, effective_config
        )
        if final_generation_config:
            body["generationConfig"] = final_generation_config

        safety_settings = self._build_safety_settings(effective_config)
        if safety_settings:
            body["safetySettings"] = safety_settings

        return RequestData(url=url, headers=headers, body=body)

    def apply_config_override(
        self,
        model: "LLMModel",
        body: dict[str, Any],
        config: "LLMGenerationConfig | None" = None,
    ) -> dict[str, Any]:
        """应用配置覆盖 - Gemini 不需要额外的配置覆盖"""
        return body

    def _get_gemini_endpoint(
        self, model: "LLMModel", config: "LLMGenerationConfig | None" = None
    ) -> str:
        """根据配置选择Gemini API端点"""
        if config:
            if getattr(config, "enable_code_execution", False):
                return f"/v1beta/models/{model.model_name}:generateContent"

            if getattr(config, "enable_grounding", False):
                return f"/v1beta/models/{model.model_name}:generateContent"

        return f"/v1beta/models/{model.model_name}:generateContent"

    def _convert_tool_choice_to_gemini(
        self, tool_choice_value: str | dict[str, Any]
    ) -> dict[str, Any]:
        """转换工具选择策略为Gemini格式"""
        if isinstance(tool_choice_value, str):
            mode_upper = tool_choice_value.upper()
            if mode_upper in ["AUTO", "NONE", "ANY"]:
                return {"functionCallingConfig": {"mode": mode_upper}}
            else:
                logger.warning(
                    f"不支持的 tool_choice 字符串值: '{tool_choice_value}'。"
                    f"回退到 AUTO。"
                )
                return {"functionCallingConfig": {"mode": "AUTO"}}

        elif isinstance(tool_choice_value, dict):
            if (
                tool_choice_value.get("type") == "function"
                and "function" in tool_choice_value
            ):
                func_name = tool_choice_value["function"].get("name")
                if func_name:
                    return {
                        "functionCallingConfig": {
                            "mode": "ANY",
                            "allowedFunctionNames": [func_name],
                        }
                    }
                else:
                    logger.warning(
                        f"tool_choice dict 中的函数名无效: {tool_choice_value}。"
                        f"回退到 AUTO。"
                    )
                    return {"functionCallingConfig": {"mode": "AUTO"}}

            elif "functionCallingConfig" in tool_choice_value:
                return {
                    "functionCallingConfig": tool_choice_value["functionCallingConfig"]
                }

            else:
                logger.warning(
                    f"不支持的 tool_choice dict 值: {tool_choice_value}。回退到 AUTO。"
                )
                return {"functionCallingConfig": {"mode": "AUTO"}}

        logger.warning(
            f"tool_choice 的类型无效: {type(tool_choice_value)}。回退到 AUTO。"
        )
        return {"functionCallingConfig": {"mode": "AUTO"}}

    def _build_gemini_generation_config(
        self, model: "LLMModel", config: "LLMGenerationConfig | None" = None
    ) -> dict[str, Any]:
        """构建Gemini生成配置"""
        effective_config = config if config is not None else model._generation_config

        if not effective_config:
            return {}

        generation_config = effective_config.to_api_params(
            api_type="gemini", model_name=model.model_name
        )

        if generation_config:
            param_keys = list(generation_config.keys())
            logger.debug(
                f"构建Gemini生成配置完成，包含 {len(generation_config)} 个参数: "
                f"{param_keys}"
            )

        return generation_config

    def _build_safety_settings(
        self, config: "LLMGenerationConfig | None" = None
    ) -> list[dict[str, Any]] | None:
        """构建安全设置"""
        if not config:
            return None

        safety_settings = []

        safety_categories = [
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT",
        ]

        custom_safety_settings = getattr(config, "safety_settings", None)
        if custom_safety_settings:
            for category, threshold in custom_safety_settings.items():
                safety_settings.append({"category": category, "threshold": threshold})
        else:
            from ..config.providers import get_gemini_safety_threshold

            threshold = get_gemini_safety_threshold()
            for category in safety_categories:
                safety_settings.append({"category": category, "threshold": threshold})

        return safety_settings if safety_settings else None

    def parse_response(
        self,
        model: "LLMModel",
        response_json: dict[str, Any],
        is_advanced: bool = False,
    ) -> ResponseData:
        """解析API响应"""
        return self._parse_response(model, response_json, is_advanced)

    def _parse_response(
        self,
        model: "LLMModel",
        response_json: dict[str, Any],
        is_advanced: bool = False,
    ) -> ResponseData:
        """解析 Gemini API 响应"""
        _ = is_advanced
        self.validate_response(response_json)

        try:
            candidates = response_json.get("candidates", [])
            if not candidates:
                logger.debug("Gemini响应中没有candidates。")
                return ResponseData(text="", raw_response=response_json)

            candidate = candidates[0]

            if candidate.get("finishReason") in [
                "RECITATION",
                "OTHER",
            ] and not candidate.get("content"):
                logger.warning(
                    f"Gemini candidate finished with reason "
                    f"'{candidate.get('finishReason')}' and no content."
                )
                return ResponseData(
                    text="",
                    raw_response=response_json,
                    usage_info=response_json.get("usageMetadata"),
                )

            content_data = candidate.get("content", {})
            parts = content_data.get("parts", [])

            text_content = ""
            parsed_tool_calls: list["LLMToolCall"] | None = None
            thought_summary_parts = []
            answer_parts = []

            for part in parts:
                if "text" in part:
                    answer_parts.append(part["text"])
                elif "thought" in part:
                    thought_summary_parts.append(part["thought"])
                elif "thoughtSummary" in part:
                    thought_summary_parts.append(part["thoughtSummary"])
                elif "functionCall" in part:
                    if parsed_tool_calls is None:
                        parsed_tool_calls = []
                    fc_data = part["functionCall"]
                    try:
                        import json

                        from ..types.models import LLMToolCall, LLMToolFunction

                        call_id = f"call_{model.provider_name}_{len(parsed_tool_calls)}"
                        parsed_tool_calls.append(
                            LLMToolCall(
                                id=call_id,
                                function=LLMToolFunction(
                                    name=fc_data["name"],
                                    arguments=json.dumps(fc_data["args"]),
                                ),
                            )
                        )
                    except KeyError as e:
                        logger.warning(
                            f"解析Gemini functionCall时缺少键: {fc_data}, 错误: {e}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"解析Gemini functionCall时出错: {fc_data}, 错误: {e}"
                        )
                elif "codeExecutionResult" in part:
                    result = part["codeExecutionResult"]
                    if result.get("outcome") == "OK":
                        output = result.get("output", "")
                        answer_parts.append(f"\n[代码执行结果]:\n```\n{output}\n```\n")
                    else:
                        answer_parts.append(
                            f"\n[代码执行失败]: {result.get('outcome', 'UNKNOWN')}\n"
                        )

            if thought_summary_parts:
                full_thought_summary = "\n".join(thought_summary_parts).strip()
                full_answer = "".join(answer_parts).strip()

                formatted_parts = []
                if full_thought_summary:
                    formatted_parts.append(f"🤔 **思考过程**\n\n{full_thought_summary}")
                if full_answer:
                    separator = "\n\n---\n\n" if full_thought_summary else ""
                    formatted_parts.append(f"{separator}✅ **回答**\n\n{full_answer}")

                text_content = "".join(formatted_parts)
            else:
                text_content = "".join(answer_parts)

            usage_info = response_json.get("usageMetadata")

            grounding_metadata_obj = None
            if grounding_data := candidate.get("groundingMetadata"):
                try:
                    from ..types.models import LLMGroundingMetadata

                    grounding_metadata_obj = LLMGroundingMetadata(**grounding_data)
                except Exception as e:
                    logger.warning(f"无法解析Grounding元数据: {grounding_data}, {e}")

            return ResponseData(
                text=text_content,
                tool_calls=parsed_tool_calls,
                usage_info=usage_info,
                raw_response=response_json,
                grounding_metadata=grounding_metadata_obj,
            )

        except Exception as e:
            logger.error(f"解析 Gemini 响应失败: {e}", e=e)
            raise LLMException(
                f"解析API响应失败: {e}",
                code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                cause=e,
            )

    def prepare_embedding_request(
        self,
        model: "LLMModel",
        api_key: str,
        texts: list[str],
        task_type: "EmbeddingTaskType | str",
        **kwargs: Any,
    ) -> RequestData:
        """准备文本嵌入请求"""
        api_model_name = model.model_name
        if not api_model_name.startswith("models/"):
            api_model_name = f"models/{api_model_name}"

        url = self.get_api_url(model, f"/{api_model_name}:batchEmbedContents")
        headers = self.get_base_headers(api_key)

        requests_payload = []
        for text_content in texts:
            request_item: dict[str, Any] = {
                "content": {"parts": [{"text": text_content}]},
            }

            from ..types.enums import EmbeddingTaskType

            if task_type and task_type != EmbeddingTaskType.RETRIEVAL_DOCUMENT:
                request_item["task_type"] = str(task_type).upper()
            if title := kwargs.get("title"):
                request_item["title"] = title
            if output_dimensionality := kwargs.get("output_dimensionality"):
                request_item["output_dimensionality"] = output_dimensionality

            requests_payload.append(request_item)

        body = {"requests": requests_payload}
        return RequestData(url=url, headers=headers, body=body)

    def parse_embedding_response(
        self, response_json: dict[str, Any]
    ) -> list[list[float]]:
        """解析文本嵌入响应"""
        try:
            embeddings_data = response_json["embeddings"]
            return [item["values"] for item in embeddings_data]
        except KeyError as e:
            logger.error(f"解析Gemini嵌入响应时缺少键: {e}. 响应: {response_json}")
            raise LLMException(
                "Gemini嵌入响应格式错误",
                code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                details={"error": str(e)},
            )
        except Exception as e:
            logger.error(
                f"解析Gemini嵌入响应时发生未知错误: {e}. 响应: {response_json}"
            )
            raise LLMException(
                f"解析Gemini嵌入响应失败: {e}",
                code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                cause=e,
            )

    def validate_embedding_response(self, response_json: dict[str, Any]) -> None:
        """验证嵌入响应"""
        super().validate_embedding_response(response_json)
        if "embeddings" not in response_json or not isinstance(
            response_json["embeddings"], list
        ):
            raise LLMException(
                "Gemini嵌入响应缺少'embeddings'字段或格式不正确",
                code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                details=response_json,
            )
        for item in response_json["embeddings"]:
            if "values" not in item:
                raise LLMException(
                    "Gemini嵌入响应的条目中缺少'values'字段",
                    code=LLMErrorCode.RESPONSE_PARSE_ERROR,
                    details=response_json,
                )
