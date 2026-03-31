"""
工具模块

整合了工具参数解析器、工具提供者管理器与工具执行逻辑，便于在 LLM 服务层统一调用。
"""

import asyncio
from collections.abc import Callable
from enum import Enum
import inspect
import json
import re
import time
from typing import (
    Annotated,
    Any,
    Optional,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)
from typing_extensions import Self, override

from httpx import NetworkError, TimeoutException

try:
    import ujson as fast_json
except ImportError:
    fast_json = json

import nonebot
from nonebot.dependencies import Dependent, Param
from nonebot.internal.adapter import Bot, Event
from nonebot.internal.params import (
    BotParam,
    DefaultParam,
    DependParam,
    DependsInner,
    EventParam,
    StateParam,
)
from pydantic import BaseModel, Field, ValidationError, create_model
from pydantic.fields import FieldInfo

from zhenxun.services.log import logger
from zhenxun.utils.decorator.retry import Retry
from zhenxun.utils.pydantic_compat import model_dump, model_fields, model_json_schema

from .types import (
    LLMErrorCode,
    LLMException,
    LLMMessage,
    LLMToolCall,
    ToolExecutable,
    ToolProvider,
    ToolResult,
)
from .types.models import ToolDefinition
from .types.protocols import BaseCallbackHandler, ToolCallData


class ToolParam(Param):
    """
    工具参数提取器。

    用于在自定义工具函数（Function Tool）中，从 LLM 解析出的参数字典
    (`state["_tool_params"]`)
    中提取特定的参数值。通常配合 `Annotated` 和依赖注入系统使用。
    """

    def __init__(self, *args: Any, name: str, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.name = name

    def __repr__(self) -> str:
        return f"ToolParam(name={self.name})"

    @classmethod
    @override
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> Optional["ToolParam"]:
        if param.default is not inspect.Parameter.empty and isinstance(
            param.default, DependsInner
        ):
            return None

        if get_origin(param.annotation) is Annotated:
            for arg in get_args(param.annotation):
                if isinstance(arg, DependsInner):
                    return None

        if param.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            return cls(name=param.name)
        return None

    @override
    async def _solve(self, **kwargs: Any) -> Any:
        state: dict[str, Any] = kwargs.get("state", {})
        tool_params = state.get("_tool_params", {})
        if self.name in tool_params:
            return tool_params[self.name]
        return None


class RunContext(BaseModel):
    """
    依赖注入容器（DI Container），保留原有上下文信息的同时提升获取类型的能力。
    """

    session_id: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True


class RunContextParam(Param):
    """自动注入 RunContext 的参数解析器"""

    @classmethod
    def _check_param(
        cls, param: inspect.Parameter, allow_types: tuple[type[Param], ...]
    ) -> Optional["RunContextParam"]:
        if param.annotation is RunContext:
            return cls()
        return None

    async def _solve(self, **kwargs: Any) -> Any:
        state = kwargs.get("state", {})
        return state.get("_agent_context")


def _parse_docstring_params(docstring: str | None) -> dict[str, str]:
    """
    解析文档字符串，提取参数描述。
    支持 Google Style (Args:), ReST Style (:param:), 和中文风格 (参数:)。
    """
    if not docstring:
        return {}

    params: dict[str, str] = {}
    lines = docstring.splitlines()

    rest_pattern = re.compile(r"[:@]param\s+(\w+)\s*:?\s*(.*)")
    found_rest = False
    for line in lines:
        match = rest_pattern.search(line)
        if match:
            params[match.group(1)] = match.group(2).strip()
            found_rest = True

    if found_rest:
        return params

    section_header_pattern = re.compile(
        r"^\s*(?:Args|Arguments|Parameters|参数)\s*[:：]\s*$"
    )

    param_section_active = False
    google_pattern = re.compile(r"^\s*(\**\w+)(?:\s*\(.*?\))?\s*[:：]\s*(.*)")

    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            continue

        if section_header_pattern.match(line):
            param_section_active = True
            continue

        if param_section_active:
            if (
                stripped_line.endswith(":") or stripped_line.endswith("：")
            ) and not google_pattern.match(line):
                param_section_active = False
                continue

            match = google_pattern.match(line)
            if match:
                name = match.group(1).lstrip("*")
                desc = match.group(2).strip()
                params[name] = desc

    return params


def _create_dynamic_model(func: Callable) -> type[BaseModel]:
    """根据函数签名动态创建 Pydantic 模型"""
    sig = inspect.signature(func)
    doc_params = _parse_docstring_params(func.__doc__)
    type_hints = get_type_hints(func, include_extras=True)

    fields = {}
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        annotation = type_hints.get(name, Any)
        default = param.default

        is_run_context = False
        if annotation is RunContext:
            is_run_context = True
        else:
            origin = get_origin(annotation)
            if origin is Union:
                args = get_args(annotation)
                if RunContext in args:
                    is_run_context = True

        if is_run_context:
            continue

        if default is not inspect.Parameter.empty and isinstance(default, DependsInner):
            continue

        if get_origin(annotation) is Annotated:
            args = get_args(annotation)
            if any(isinstance(arg, DependsInner) for arg in args):
                continue

        description = doc_params.get(name)
        if isinstance(default, FieldInfo):
            if description and not getattr(default, "description", None):
                default.description = description
            fields[name] = (annotation, default)
        else:
            if default is inspect.Parameter.empty:
                default = ...
            fields[name] = (annotation, Field(default, description=description))

    return create_model(f"{func.__name__}Params", **fields)


class FunctionExecutable(ToolExecutable):
    """一个 ToolExecutable 的实现，用于包装一个普通的 Python 函数。"""

    def __init__(
        self,
        func: Callable,
        name: str,
        description: str,
        params_model: type[BaseModel] | None = None,
        unpack_args: bool = False,
    ):
        self._func = func
        self._name = name
        self._description = description
        self._params_model = params_model
        self._unpack_args = unpack_args

        self.dependent = Dependent[Any].parse(
            call=func,
            allow_types=(
                DependParam,
                BotParam,
                EventParam,
                StateParam,
                RunContextParam,
                ToolParam,
                DefaultParam,
            ),
        )

    async def get_definition(self) -> ToolDefinition:
        if not self._params_model:
            return ToolDefinition(
                name=self._name,
                description=self._description,
                parameters={"type": "object", "properties": {}},
            )

        schema = model_json_schema(self._params_model)

        return ToolDefinition(
            name=self._name,
            description=self._description,
            parameters={
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        )

    async def execute(
        self, context: RunContext | None = None, **kwargs: Any
    ) -> ToolResult:
        context = context or RunContext()

        tool_arguments = kwargs

        if self._params_model:
            try:
                _fields = model_fields(self._params_model)
                if isinstance(_fields, dict):
                    field_names = set(_fields)
                else:
                    field_names = {
                        name
                        for field in _fields
                        for name in (
                            getattr(field, "name", None),
                            getattr(field, "alias", None),
                        )
                        if name
                    }
                validation_input = {
                    key: value for key, value in kwargs.items() if key in field_names
                }

                validated_params = self._params_model(**validation_input)

                if not self._unpack_args:
                    pass
                else:
                    validated_dict = model_dump(validated_params)
                    tool_arguments = validated_dict

            except ValidationError as e:
                error_msgs = []
                for err in e.errors():
                    loc = ".".join(str(x) for x in err["loc"])
                    msg = err["msg"]
                    error_msgs.append(f"Parameter '{loc}': {msg}")

                formatted_error = "; ".join(error_msgs)
                error_payload = {
                    "error_type": "InvalidArguments",
                    "message": f"Parameter validation failed: {formatted_error}",
                    "is_retryable": True,
                }
                return ToolResult(
                    output=json.dumps(error_payload, ensure_ascii=False),
                    display_content=f"Validation Error: {formatted_error}",
                )
            except Exception as e:
                logger.error(
                    f"执行工具 '{self._name}' 时参数验证或实例化失败: {e}", e=e
                )
                raise

        state = {
            "_tool_params": tool_arguments,
            "_agent_context": context,
        }

        bot: Bot | None = None
        if context and context.scope.get("bot"):
            bot = context.scope.get("bot")
        if not bot:
            try:
                bot = nonebot.get_bot()
            except ValueError:
                pass

        event: Event | None = None
        if context and context.scope.get("event"):
            event = context.scope.get("event")

        raw_result = await self.dependent(
            bot=bot,
            event=event,
            state=state,
        )

        return ToolResult(output=raw_result, display_content=str(raw_result))


class BuiltinFunctionToolProvider(ToolProvider):
    """一个内置的 ToolProvider，用于处理通过装饰器注册的函数。"""

    def __init__(self):
        self._functions: dict[str, dict[str, Any]] = {}

    def register(
        self,
        name: str,
        func: Callable,
        description: str,
        params_model: type[BaseModel] | None = None,
        unpack_args: bool = False,
    ):
        self._functions[name] = {
            "func": func,
            "description": description,
            "params_model": params_model,
            "unpack_args": unpack_args,
        }

    async def initialize(self) -> None:
        pass

    async def discover_tools(
        self,
        allowed_servers: list[str] | None = None,
        excluded_servers: list[str] | None = None,
    ) -> dict[str, ToolExecutable]:
        executables = {}
        for name, info in self._functions.items():
            executables[name] = FunctionExecutable(
                func=info["func"],
                name=name,
                description=info["description"],
                params_model=info["params_model"],
                unpack_args=info.get("unpack_args", False),
            )
        return executables

    async def get_tool_executable(
        self, name: str, config: dict[str, Any]
    ) -> ToolExecutable | None:
        if config.get("type", "function") == "function" and name in self._functions:
            info = self._functions[name]
            return FunctionExecutable(
                func=info["func"],
                name=name,
                description=info["description"],
                params_model=info["params_model"],
                unpack_args=info.get("unpack_args", False),
            )
        return None


class ToolProviderManager:
    """工具提供者的中心化管理器，采用单例模式。"""

    _instance: "ToolProviderManager | None" = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cast(Self, cls._instance)

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return

        self._providers: list[ToolProvider] = []
        self._resolved_tools: dict[str, ToolExecutable] | None = None
        self._init_lock = asyncio.Lock()
        self._init_promise: asyncio.Task | None = None
        self._builtin_function_provider = BuiltinFunctionToolProvider()
        self.register(self._builtin_function_provider)
        self._initialized = True

    def register(self, provider: ToolProvider):
        """注册一个新的 ToolProvider。"""
        if provider not in self._providers:
            self._providers.append(provider)
            logger.info(f"已注册工具提供者: {provider.__class__.__name__}")

    def function_tool(
        self,
        name: str,
        description: str,
        params_model: type[BaseModel] | None = None,
    ):
        """装饰器：将一个函数注册为内置工具。"""

        def decorator(func: Callable):
            if name in self._builtin_function_provider._functions:
                logger.warning(f"正在覆盖已注册的函数工具: {name}")

            final_model = params_model
            unpack_args = False
            if final_model is None:
                final_model = _create_dynamic_model(func)
                unpack_args = True

            self._builtin_function_provider.register(
                name=name,
                func=func,
                description=description,
                params_model=final_model,
                unpack_args=unpack_args,
            )
            logger.info(f"已注册函数工具: '{name}'")
            return func

        return decorator

    async def initialize(self) -> None:
        """懒加载初始化所有已注册的 ToolProvider。"""
        if not self._init_promise:
            async with self._init_lock:
                if not self._init_promise:
                    self._init_promise = asyncio.create_task(
                        self._initialize_providers()
                    )
        await self._init_promise

    async def _initialize_providers(self) -> None:
        """内部初始化逻辑。"""
        logger.info(f"开始初始化 {len(self._providers)} 个工具提供者...")
        init_tasks = [provider.initialize() for provider in self._providers]
        await asyncio.gather(*init_tasks, return_exceptions=True)
        logger.info("所有工具提供者初始化完成。")

    async def get_resolved_tools(
        self,
        allowed_servers: list[str] | None = None,
        excluded_servers: list[str] | None = None,
    ) -> dict[str, ToolExecutable]:
        """
        获取所有已发现和解析的工具。
        此方法会触发懒加载初始化，并根据是否传入过滤器来决定是否使用全局缓存。
        """
        await self.initialize()

        has_filters = allowed_servers is not None or excluded_servers is not None

        if not has_filters and self._resolved_tools is not None:
            logger.debug("使用全局工具缓存。")
            return self._resolved_tools

        if has_filters:
            logger.info("检测到过滤器，执行临时工具发现 (不使用缓存)。")
            logger.debug(
                f"过滤器详情: allowed_servers={allowed_servers}, "
                f"excluded_servers={excluded_servers}"
            )
        else:
            logger.info("未应用过滤器，开始全局工具发现...")

        all_tools: dict[str, ToolExecutable] = {}

        discover_tasks = []
        for provider in self._providers:
            sig = inspect.signature(provider.discover_tools)
            params_to_pass = {}
            if "allowed_servers" in sig.parameters:
                params_to_pass["allowed_servers"] = allowed_servers
            if "excluded_servers" in sig.parameters:
                params_to_pass["excluded_servers"] = excluded_servers

            discover_tasks.append(provider.discover_tools(**params_to_pass))

        results = await asyncio.gather(*discover_tasks, return_exceptions=True)

        for i, provider_result in enumerate(results):
            provider_name = self._providers[i].__class__.__name__
            if isinstance(provider_result, dict):
                logger.debug(
                    f"提供者 '{provider_name}' 发现了 {len(provider_result)} 个工具。"
                )
                for name, executable in provider_result.items():
                    if name in all_tools:
                        logger.warning(
                            f"发现重复的工具名称 '{name}'，后发现的将覆盖前者。"
                        )
                    all_tools[name] = executable
            elif isinstance(provider_result, Exception):
                logger.error(
                    f"提供者 '{provider_name}' 在发现工具时出错: {provider_result}"
                )

        if not has_filters:
            self._resolved_tools = all_tools
            logger.info(f"全局工具发现完成，共找到并缓存了 {len(all_tools)} 个工具。")
        else:
            logger.info(f"带过滤器的工具发现完成，共找到 {len(all_tools)} 个工具。")

        return all_tools

    async def resolve_specific_tools(
        self, tool_names: list[str]
    ) -> dict[str, ToolExecutable]:
        """
        仅解析指定名称的工具，避免触发全量工具发现。
        """
        resolved: dict[str, ToolExecutable] = {}
        if not tool_names:
            return resolved

        await self.initialize()

        for name in tool_names:
            config: dict[str, Any] = {"name": name}
            for provider in self._providers:
                try:
                    executable = await provider.get_tool_executable(name, config)
                except Exception as exc:
                    logger.error(
                        f"provider '{provider.__class__.__name__}' 在解析工具 '{name}'"
                        f"时出错: {exc}",
                        e=exc,
                    )
                    continue

                if executable:
                    resolved[name] = executable
                    break
            else:
                logger.warning(f"没有找到名为 '{name}' 的工具，已跳过。")

        return resolved

    async def get_function_tools(
        self, names: list[str] | None = None
    ) -> dict[str, ToolExecutable]:
        """
        仅从内置的函数提供者中解析指定的工具。
        """
        all_function_tools = await self._builtin_function_provider.discover_tools()
        if names is None:
            return all_function_tools

        resolved_tools = {}
        for name in names:
            if name in all_function_tools:
                resolved_tools[name] = all_function_tools[name]
            else:
                logger.warning(
                    f"本地函数工具 '{name}' 未通过 @function_tool 注册，将被忽略。"
                )
        return resolved_tools


tool_provider_manager = ToolProviderManager()
function_tool = tool_provider_manager.function_tool


class ToolErrorType(str, Enum):
    """结构化工具错误的类型枚举。"""

    TOOL_NOT_FOUND = "ToolNotFound"
    INVALID_ARGUMENTS = "InvalidArguments"
    EXECUTION_ERROR = "ExecutionError"
    USER_CANCELLATION = "UserCancellation"


class ToolErrorResult(BaseModel):
    """一个结构化的工具执行错误模型。"""

    error_type: ToolErrorType = Field(..., description="错误的类型。")
    message: str = Field(..., description="对错误的详细描述。")
    is_retryable: bool = Field(False, description="指示这个错误是否可能通过重试解决。")


class ToolInvoker:
    """
    全能工具执行器。
    负责接收工具调用请求，解析参数，触发回调，执行工具，并返回标准化的结果。
    """

    def __init__(self, callbacks: list[BaseCallbackHandler] | None = None):
        self.callbacks = callbacks or []

    async def _trigger_callbacks(self, event_name: str, *args, **kwargs: Any) -> None:
        if not self.callbacks:
            return
        tasks = [
            getattr(handler, event_name)(*args, **kwargs)
            for handler in self.callbacks
            if hasattr(handler, event_name)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def execute_tool_call(
        self,
        tool_call: LLMToolCall,
        available_tools: dict[str, ToolExecutable],
        context: Any | None = None,
    ) -> tuple[LLMToolCall, ToolResult]:
        tool_name = tool_call.function.name
        arguments_str = tool_call.function.arguments
        arguments: dict[str, Any] = {}

        try:
            if arguments_str:
                arguments = json.loads(arguments_str)
        except json.JSONDecodeError as e:
            error_result = ToolErrorResult(
                error_type=ToolErrorType.INVALID_ARGUMENTS,
                message=f"参数解析失败: {e}",
                is_retryable=False,
            )
            return tool_call, ToolResult(output=model_dump(error_result))

        tool_data = ToolCallData(tool_name=tool_name, tool_args=arguments)
        pre_calculated_result: ToolResult | None = None
        for handler in self.callbacks:
            res = await handler.on_tool_start(tool_call, tool_data)
            if isinstance(res, ToolCallData):
                tool_data = res
                arguments = tool_data.tool_args
                tool_call.function.arguments = json.dumps(arguments, ensure_ascii=False)
            elif isinstance(res, ToolResult):
                pre_calculated_result = res
                break

        if pre_calculated_result:
            return tool_call, pre_calculated_result

        executable = available_tools.get(tool_name)
        if not executable:
            error_result = ToolErrorResult(
                error_type=ToolErrorType.TOOL_NOT_FOUND,
                message=f"Tool '{tool_name}' not found.",
                is_retryable=False,
            )
            return tool_call, ToolResult(output=model_dump(error_result))

        from .config.providers import get_llm_config

        if not get_llm_config().debug_log:
            try:
                definition = await executable.get_definition()
                schema_payload = getattr(definition, "parameters", {})
                schema_json = fast_json.dumps(
                    schema_payload,
                    ensure_ascii=False,
                )
                logger.debug(
                    f"🔍 [JIT Schema] {tool_name}: {schema_json}",
                    "ToolInvoker",
                )
            except Exception as e:
                logger.trace(f"JIT Schema logging failed: {e}")

        start_t = time.monotonic()
        result: ToolResult | None = None
        error: Exception | None = None

        try:

            @Retry.simple(stop_max_attempt=2, wait_fixed_seconds=1)
            async def execute_with_retry():
                return await executable.execute(context=context, **arguments)

            result = await execute_with_retry()
        except ValidationError as e:
            error = e
            error_msgs = []
            for err in e.errors():
                loc = ".".join(str(x) for x in err["loc"])
                msg = err["msg"]
                error_msgs.append(f"参数 '{loc}': {msg}")

            formatted_error = "; ".join(error_msgs)
            error_result = ToolErrorResult(
                error_type=ToolErrorType.INVALID_ARGUMENTS,
                message=f"参数验证失败。请根据错误修正你的输入: {formatted_error}",
                is_retryable=True,
            )
            result = ToolResult(output=model_dump(error_result))
        except (TimeoutException, NetworkError) as e:
            error = e
            error_result = ToolErrorResult(
                error_type=ToolErrorType.EXECUTION_ERROR,
                message=f"工具执行网络超时或连接失败: {e!s}",
                is_retryable=False,
            )
            result = ToolResult(output=model_dump(error_result))
        except Exception as e:
            error = e
            error_type = ToolErrorType.EXECUTION_ERROR
            if (
                isinstance(e, LLMException)
                and e.code == LLMErrorCode.CONFIGURATION_ERROR
            ):
                error_type = ToolErrorType.TOOL_NOT_FOUND
                is_retryable = False

            is_retryable = False

            error_result = ToolErrorResult(
                error_type=error_type, message=str(e), is_retryable=is_retryable
            )
            result = ToolResult(output=model_dump(error_result))

        duration = time.monotonic() - start_t

        await self._trigger_callbacks(
            "on_tool_end",
            result=result,
            error=error,
            tool_call=tool_call,
            duration=duration,
        )

        if result is None:
            raise LLMException("工具执行未返回任何结果。")

        return tool_call, result

    async def execute_batch(
        self,
        tool_calls: list[LLMToolCall],
        available_tools: dict[str, ToolExecutable],
        context: Any | None = None,
    ) -> list[LLMMessage]:
        if not tool_calls:
            return []

        tasks = [
            self.execute_tool_call(call, available_tools, context)
            for call in tool_calls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tool_messages: list[LLMMessage] = []
        for index, result_pair in enumerate(results):
            original_call = tool_calls[index]

            if isinstance(result_pair, Exception):
                logger.error(
                    f"工具执行发生未捕获异常: {original_call.function.name}, "
                    f"错误: {result_pair}"
                )
                tool_messages.append(
                    LLMMessage.tool_response(
                        tool_call_id=original_call.id,
                        function_name=original_call.function.name,
                        result={
                            "error": f"System Execution Error: {result_pair}",
                            "status": "failed",
                        },
                    )
                )
                continue

            tool_call_result = cast(tuple[LLMToolCall, ToolResult], result_pair)
            _, tool_result = tool_call_result
            tool_messages.append(
                LLMMessage.tool_response(
                    tool_call_id=original_call.id,
                    function_name=original_call.function.name,
                    result=tool_result.output,
                )
            )
        return tool_messages


__all__ = [
    "RunContext",
    "RunContextParam",
    "ToolErrorResult",
    "ToolErrorType",
    "ToolInvoker",
    "ToolParam",
    "function_tool",
    "tool_provider_manager",
]
