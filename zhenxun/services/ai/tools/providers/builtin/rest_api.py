import json
from typing import Any, Literal

import httpx

from zhenxun.services.ai.core.stream_events import ToolStreamChunkEvent
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.core.decorators import tool
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import ToolResult
from zhenxun.services.log import logger


class RestApiToolkit(BaseToolkit):
    """
    通用 REST API 工具箱。
    让大模型无需编写任何代码即可调用外部 HTTP 接口获取或提交数据。
    """

    default_prefix = ""

    default_instructions = (
        "你拥有调用外部 REST API 接口的能力。\n"
        "1. 使用 `make_request` 工具发起 HTTP 请求。\n"
        "2. 如果请求失败，请分析返回的错误信息。可能需要调整请求参数 (params) 或请求体 (json_data)。\n"  # noqa: E501
        "3. 务必根据返回的 JSON 数据，提取对用户有用的信息进行回答。"
    )

    def __init__(
        self,
        base_url: str = "",
        default_headers: dict[str, str] | None = None,
        default_params: dict[str, Any] | None = None,
        timeout: int = 30,
        **kwargs: Any,
    ):
        """
        初始化通用 REST API 工具箱。

        参数：
            base_url: 接口基础 URL，默认发起请求时将与其拼接。
            default_headers: 发起请求时携带的默认 Headers 请求头。
            default_params: 发起请求时携带的默认 URL 查询参数。
            timeout: 请求超时时间，单位为秒。
            kwargs: 其他透传给 BaseToolkit 的参数。
        """
        super().__init__(**kwargs)
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.default_headers = default_headers or {}
        self.default_params = default_params or {}
        self.timeout = timeout

    @tool(name="make_request")
    async def make_request(
        self,
        endpoint: str,
        method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = "GET",
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        context: RunContext | None = None,
    ) -> ToolResult:
        """
        发起 HTTP 请求获取外部数据。

        Args:
            endpoint: API的路径(若初始化了base_url，将自动拼接)。如果是一个完整的URL(以http开头)，则直接请求该URL。
            method: HTTP 请求方法。
            params: URL 查询参数字典。
            json_data: JSON 请求体字典。
        """  # noqa: E501
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            url = endpoint
        else:
            url = (
                f"{self.base_url}/{endpoint.lstrip('/')}" if self.base_url else endpoint
            )

        merged_params = self.default_params.copy()
        if params:
            merged_params.update(params)

        logger.info(f"🌐 [RestApiToolkit] 正在请求 {method} {url}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    params=merged_params,
                    json=json_data,
                    headers=self.default_headers,
                )

            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = response.text

            result_dict = {"status_code": response.status_code, "data": response_data}

            is_error = response.status_code >= 400
            result = ToolResult(output=result_dict)

            if context and context.run.event_bus:
                msg = (
                    f"🌐 已调用 API: {url}"
                    if not is_error
                    else f"❌ API 调用失败 (Status: {response.status_code})"
                )
                await context.run.event_bus.emit(
                    ToolStreamChunkEvent(
                        tool_name=context.call.tool_name if context else "make_request",
                        content=msg,
                    )
                )

            if is_error:
                result = result.as_error()
            return result

        except Exception as e:
            logger.error(f"RestApiToolkit 请求失败: {e}")
            if context and context.run.event_bus:
                await context.run.event_bus.emit(
                    ToolStreamChunkEvent(
                        tool_name=context.call.tool_name if context else "make_request",
                        content="❌ API 网络请求发生框架级错误",
                    )
                )
            return ToolResult(
                output=f"网络请求失败: {type(e).__name__} - {e}"
            ).as_error()
