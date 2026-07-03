from collections.abc import Callable
import copy
import hashlib
import inspect
import json
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from zhenxun.services.ai.capabilities import CombinedCapability
from zhenxun.services.ai.core.exceptions import (
    ControlFlowExit,
    NeedsInputException,
    ToolFatalError,
    ToolRetryError,
)
from zhenxun.services.ai.core.models import ToolDefinition
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.core.capabilities import InteractiveCapability
from zhenxun.services.ai.tools.models import (
    ResolvedToolPayload,
    ToolOptions,
    ToolResult,
)
from zhenxun.services.ai.utils.utils import wrap_to_async
from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_dump, model_json_schema, model_validate

from .schema import (
    _parse_docstring,
    build_schema_hint,
    build_tool_model,
    check_field_permissions,
    prune_schema_by_permissions,
)

_tool_runner_class: type | None = None


def register_tool_runner(runner_class: type) -> None:
    global _tool_runner_class
    _tool_runner_class = runner_class


class BaseTool:
    """
    面向对象的工具基类。支持多模态契约与纯 Pydantic V2 参数验证。

    参数:
        name: 工具名称。大模型将基于此识别并调用。如果不提供，默认使用类名。
        description: 工具的说明文档。大模型会根据此描述理解工具用途。
        settings: 配置选项（包含缓存、拦截、中间件等）。
    """

    name: str
    description: str
    settings: ToolOptions
    execution_side: Literal["client", "server"] = "client"
    current_usage_count: int = 0
    _dynamic_def: Any = None
    args_schema: type[BaseModel] | None = None
    _base_schema: dict[str, Any] | None = None
    _param_model: Any = None
    parent_toolkit: Any = None

    @property
    def metadata(self) -> dict[str, Any]:
        return self.settings.metadata

    @metadata.setter
    def metadata(self, value: dict[str, Any]):
        self.settings.metadata = value

    def get_execute_target(self) -> Callable:
        """获取依赖注入和实际执行的目标函数。子类可重写（如 FunctionTool）。"""
        if not hasattr(self, "run"):
            raise NotImplementedError(
                f"工具 {self.__class__.__name__} 未实现 run 方法。"
            )
        return self.run

    def get_signature_target(self) -> Callable:
        """获取依赖注入和 Schema 生成的原始函数目标。子类可重写。"""
        return self.get_execute_target()

    async def run(self, **kwargs: Any) -> Any:
        """供第三方开发者重写的核心执行逻辑。完美支持 Inject 依赖注入。"""
        raise NotImplementedError("子类必须实现 run 方法")

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        settings: ToolOptions | None = None,
    ):
        """
        初始化工具基类。

        参数:
            name: 工具名，供大模型识别调用，默认取类名。
            description: 工具功能说明，供大模型理解，默认取 __doc__ 说明。
            settings: 工具的个性化配置选项（例如缓存时间、依赖中间件等），默认 None。
        """
        self.name = name or getattr(self, "name", self.__class__.__name__)
        self.description = description or getattr(
            self, "description", self.__doc__ or "未提供描述"
        )
        self.settings = settings or getattr(self, "settings", ToolOptions())
        self.args_schema = self.settings.args_schema or getattr(
            self.__class__, "args_schema", None
        )
        self.current_usage_count = 0

        class_meta = getattr(self.__class__, "metadata", {})
        if class_meta and not isinstance(class_meta, property):
            self.settings.metadata.update(class_meta)

    def clone_with_options(self, override: Any) -> "BaseTool":
        """创建当前工具的浅拷贝，并覆盖 ToolOptions 和基本属性"""
        new_tool = copy.copy(self)

        if hasattr(override, "to_tool_options"):
            override_settings = override.to_tool_options()
        else:
            override_settings = getattr(
                override, "options", getattr(override, "settings", None)
            )

        if override_settings:
            new_tool.settings = self.settings.merge(override_settings)

        new_name = getattr(override, "new_name", None)
        if new_name:
            new_tool.name = new_name

        new_desc = getattr(override, "description", None)
        if new_desc:
            new_tool.description = new_desc

        return new_tool

    async def _handle_validation_error(self, e: ValidationError, kwargs: dict) -> None:
        """
        将 Pydantic 参数校验失败转化为 ToolRetryError 或 NeedsInputException
        """
        error_msgs = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "root"
            msg = err.get("msg", "")
            ctx = err.get("ctx", {})
            ctx_str = f" (期望要求: {ctx})" if ctx else ""
            error_msgs.append(f"参数字段 `{loc}`: {msg}{ctx_str}")

        err_msg = "\n".join(error_msgs)
        validation_model = getattr(self, "args_schema", None) or getattr(
            self, "_param_model", None
        )
        schema_hint = build_schema_hint(validation_model)

        if any(
            isinstance(c, InteractiveCapability) for c in self.settings.capabilities
        ):
            first_error = e.errors()[0]
            field_name = (
                str(first_error["loc"][-1]) if first_error.get("loc") else "unknown"
            )
            field_desc = f"参数验证失败: {first_error['msg']}"

            raise NeedsInputException(field_name, field_desc, kwargs)

        raise ToolRetryError(
            f"参数验证未通过，请严格根据以下错误信息修正参数后重试：\n{err_msg}\n"
            f"（附：你刚才传入的非法参数为 {kwargs}）{schema_hint}"
        )

    async def get_definition(
        self, context: RunContext | None = None
    ) -> ToolDefinition | None:
        """获取工具的 JSON Schema 定义。支持动态修改 Schema 甚至隐藏工具。"""
        if hasattr(self, "_dynamic_def") and self._dynamic_def is not None:
            return self._dynamic_def

        args_schema = getattr(self, "args_schema", None)
        if args_schema is not None:
            if context is not None:
                base_schema = model_json_schema(args_schema)
                schema = await prune_schema_by_permissions(
                    args_schema, context, base_schema
                )
            else:
                schema = model_json_schema(args_schema)
        elif getattr(self, "_base_schema", None) is not None:
            schema = (
                self._base_schema.copy()
                if isinstance(self._base_schema, dict)
                else {"type": "object", "properties": {}}
            )
        else:
            schema = {"type": "object", "properties": {}}

        if getattr(self.settings, "require_intent", False):
            if "properties" not in schema:
                schema["properties"] = {}
            if "_intent" not in schema["properties"]:
                new_props = {
                    "_intent": {
                        "type": "string",
                        "description": (
                            "调用此工具前，必须在此字段简述你的意图、目的或思考过程"
                        ),
                    }
                }
                new_props.update(schema["properties"])
                schema["properties"] = new_props

                if "required" not in schema:
                    schema["required"] = []
                if "_intent" not in schema["required"]:
                    schema["required"].insert(0, "_intent")

        tool_def = ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=schema,
            metadata=self.settings.metadata.copy(),
        )

        if context and self.settings.capabilities:
            combined_cap = CombinedCapability(self.settings.capabilities)
            defs = await combined_cap.prepare_tools(context, [tool_def])
            if not defs:
                return None
            tool_def = defs[0]

        return tool_def

    async def resolve(self, context: RunContext | None = None) -> ResolvedToolPayload:
        """实现 ToolResolvable 协议，将自身解析为标准 Payload"""
        definition = await self.get_definition(context)
        if definition is None:
            return ResolvedToolPayload()

        run_scoped_tool = copy.copy(self)
        run_scoped_tool._dynamic_def = definition
        if not hasattr(run_scoped_tool, "name"):
            setattr(run_scoped_tool, "name", definition.name)

        return ResolvedToolPayload(tools=[run_scoped_tool])

    def _generate_cache_key(self, arguments: dict[str, Any]) -> str:
        """
        根据工具名称和参数生成绝对唯一的缓存 Key。
        通过对字典 key 进行排序并转化为 json 字符串，
        保证参数顺序不同但内容相同时 Hash 结果一致。
        """
        args_str = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        key_str = f"{self.name}:{args_str}"
        return hashlib.md5(key_str.encode("utf-8")).hexdigest()

    async def validate_args(
        self, kwargs: dict[str, Any], context: RunContext | None = None
    ) -> dict[str, Any]:
        """验证参数，返回序列化后的合法字典。如果校验失败则抛出异常。"""
        validation_model = getattr(self, "args_schema", None) or getattr(
            self, "_param_model", None
        )
        if validation_model:
            if context:
                await check_field_permissions(validation_model, kwargs, context)
            try:
                validated_model = model_validate(validation_model, kwargs)
                return model_dump(validated_model, exclude_unset=True)
            except ValidationError as e:
                await self._handle_validation_error(e, kwargs)
        return kwargs

    async def execute(
        self, context: RunContext | None = None, **kwargs: Any
    ) -> ToolResult:
        """工具执行的总入口，负责组装和调度中间件管道"""
        context_to_pass = context or RunContext()

        try:
            return await self._core_execution(context_to_pass, **kwargs)
        except Exception as e:
            if not isinstance(e, ControlFlowExit):
                logger.error(f"工具 {self.name} 执行抛出异常，将交由底层引擎处理: {e}")
            raise

    async def _core_execution(self, context: RunContext, **kwargs: Any) -> ToolResult:
        """核心执行流水线 (Core Execution Pipeline)"""
        _retries = context.run.tool_retries.get(self.name, 0)

        if (
            self.settings.max_usage_count is not None
            and self.current_usage_count >= self.settings.max_usage_count
        ):
            raise ToolFatalError(
                f"工具 '{self.name}' 已达到最大调用次数上限 ({self.settings.max_usage_count})"  # noqa: E501
            )
        self.current_usage_count += 1

        if _tool_runner_class is None:
            raise ToolFatalError(
                f"工具 '{self.name}' 运行时错误：未注册 ToolRunner 执行器。"
            )

        runner = _tool_runner_class()
        final_result = await runner.run(tool=self, context=context, **kwargs)

        if not final_result.is_error:
            context.run.tool_retries[self.name] = 0

        return final_result


class ServerSideTool(BaseTool):
    """
    云端原生工具的抽象基类。
    代表那些由各大模型厂商在云端原生实现的能力（如 Google Search, Code Execution）。
    此类工具只产生 Schema 提交给大模型用于触发路由，禁止在本地执行。
    """

    execution_side: Literal["client", "server"] = "server"
    type_id: str = ""

    async def execute(
        self, context: RunContext | None = None, **kwargs: Any
    ) -> ToolResult:
        raise ToolFatalError(
            f"[{self.name}] 是服务端工具，禁止在本地层执行引擎中运行。"
        )

    async def get_definition(
        self, context: RunContext | None = None
    ) -> ToolDefinition | None:
        if hasattr(self, "_dynamic_def") and self._dynamic_def is not None:
            return self._dynamic_def
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={"type": "object", "properties": {}},
            metadata=self.settings.metadata.copy(),
        )


class FunctionTool(BaseTool):
    """
    将普通 Python 函数包装为 BaseTool 的实现。

    参数:
        func: 实际执行的 Python 异步或同步函数。
        name: 覆盖函数名的工具名称。
        description: 覆盖函数 docstring 的工具描述。
        settings: 配置选项。
    """

    def __init__(
        self,
        func: Callable,
        name: str | None = None,
        description: str | None = None,
        settings: ToolOptions | None = None,
    ):
        """
        初始化函数式包装工具。

        参数:
            func: 被包装的底层 Python 同步或异步函数。
            name: 覆盖函数名的自定义工具名，默认 None。
            description: 覆盖 docstring 的自定义工具描述，默认 None。
            settings: 覆盖默认选项的自定义工具配置项，默认 None。
        """
        super().__init__(name=name, description=description, settings=settings)

        self._original_func = func
        self.__name__ = self.name
        self._func = wrap_to_async(func)
        self._schema_built = False

        main_desc, _ = _parse_docstring(self._original_func.__doc__)
        if not description or description == "未提供描述":
            self.description = main_desc or "未提供描述"

    def get_execute_target(self) -> Callable:
        """FunctionTool 的执行目标和依赖注入目标是其包装的原始函数"""
        return self._func

    def get_signature_target(self) -> Callable:
        return self._original_func

    async def run(self, **kwargs: Any) -> Any:
        """保持接口完整性，但实际执行由 _core_execution 导向 _func"""
        is_async_gen = getattr(
            self._func, "_is_async_gen", False
        ) or inspect.isasyncgenfunction(self._func)
        if is_async_gen:
            res = []
            async for chunk in self._func(**kwargs):
                res.append(chunk)
            return res
        return await self._func(**kwargs)

    def _ensure_schema(self):
        """JIT 懒加载：推迟到第一次请求定义或验证参数时再构建 Schema (耗时操作)"""
        if not self._schema_built:
            if self.args_schema:
                self._base_schema = model_json_schema(self.args_schema)
                self._param_model = self.args_schema
            else:
                schema, model = build_tool_model(
                    self.get_signature_target(), strict=self.settings.strict
                )
                self._base_schema = schema
                self._param_model = model
            self._schema_built = True

    async def get_definition(
        self, context: RunContext | None = None
    ) -> ToolDefinition | None:
        self._ensure_schema()
        return await super().get_definition(context)

    async def validate_args(
        self, kwargs: dict[str, Any], context: RunContext | None = None
    ) -> dict[str, Any]:
        self._ensure_schema()
        return await super().validate_args(kwargs, context=context)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """允许像普通函数一样被直接调用。对其他插件完全透明。"""
        return self._original_func(*args, **kwargs)
