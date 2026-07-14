from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, cast

from arclet.alconna import Alconna, Subcommand
from nonebot.adapters import Bot, Event
from nonebot.exception import FinishedException, PausedException, RejectedException
from nonebot.internal.matcher import Matcher
from nonebot.typing import T_State
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.consts import (
    ALCONNA_EXEC_RESULT,
    ALCONNA_EXTENSION,
    ALCONNA_RESULT,
)
from nonebot_plugin_alconna.model import CommandResult
from pydantic import BaseModel

from zhenxun.services.ai.core.exceptions import ToolRetryError
from zhenxun.services.ai.core.models import ToolDefinition
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.core.schema import build_schema_hint
from zhenxun.services.ai.tools.core.tool import BaseTool
from zhenxun.services.ai.tools.models import EndRunResult, ToolOptions, ToolResult
from zhenxun.services.ai.utils.logger import log_tool as logger
from zhenxun.utils.pydantic_compat import model_validate
from zhenxun.utils.utils import infer_plugin_namespace


class MatcherAdapter(ABC):
    """Matcher 桥接适配器基类 (Adapter Pattern)"""

    def __init__(
        self,
        matcher: type[Matcher],
        args_schema: type[BaseModel] | None,
        command_formatter: Callable[[Any], list[Any] | str] | None = None,
    ):
        """
        初始化 Matcher 桥接适配器。

        参数:
            matcher: 目标 Matcher 类，负责实际接收 Fake State 并执行命令。
            args_schema: 描述 LLM 需要填写的参数 Pydantic Schema，可以为 None。
            command_formatter: 可选的自定义命令行格式化器，将参数序列化为命令行参数。
        """
        self.matcher = matcher
        self.args_schema = args_schema
        self.command_formatter = command_formatter

    def modify_tool_definition(self, tool_def: ToolDefinition) -> ToolDefinition:
        """用于清洗大模型不需要的插件内部元数据"""
        return tool_def

    @abstractmethod
    async def build_state(
        self, kwargs: dict[str, Any], bot: Bot, event: Event
    ) -> T_State:
        """根据传入参数构造目标 Matcher 需要的 Fake State"""
        raise NotImplementedError


class AlconnaAdapter(MatcherAdapter):
    """针对 nonebot_plugin_alconna 的高级解析适配器"""

    def modify_tool_definition(self, tool_def: ToolDefinition) -> ToolDefinition:
        if tool_def and tool_def.parameters and "properties" in tool_def.parameters:
            for prop in tool_def.parameters["properties"].values():
                if isinstance(prop, dict):
                    prop.pop("alc_dest", None)
                    prop.pop("alc_name", None)
        return tool_def

    def _build_kwargs_mapping(
        self, kwargs: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
        mapped_kwargs = {}
        name_overrides = {}
        raw_key_to_dest: dict[str, str] = {}

        fields = (
            getattr(
                self.args_schema,
                "model_fields",
                getattr(self.args_schema, "__fields__", {}),
            )
            if self.args_schema
            else {}
        )

        for k, v in kwargs.items():
            alc_dest = k
            alc_name = None

            if k in fields:
                field = fields[k]
                extra = getattr(
                    field,
                    "json_schema_extra",
                    getattr(getattr(field, "field_info", None), "extra", None),
                )
                if isinstance(extra, dict):
                    alc_dest = extra.get("alc_dest", k)
                    alc_name = extra.get("alc_name")

            mapped_kwargs[alc_dest] = v
            raw_key_to_dest[k] = alc_dest
            if alc_name:
                name_overrides[alc_dest] = alc_name

        return mapped_kwargs, name_overrides, raw_key_to_dest

    def _auto_infer_argv(self, kwargs: dict[str, Any]) -> tuple[list[Any], list[str]]:
        command = getattr(self.matcher, "command", lambda: None)()
        if not command:
            raise RuntimeError(f"Matcher {self.matcher} 并非合法的 AlconnaMatcher")
        command = cast(Alconna, command)

        mapped_kwargs, name_overrides, raw_key_to_dest = self._build_kwargs_mapping(
            kwargs
        )
        consumed_dests = set()

        argv = []
        cmd_name = command.command or ""
        prefixes = command.prefixes
        if prefixes and isinstance(prefixes, list):
            prefix = next((p for p in prefixes if p), "")
            argv.append(f"{prefix}{cmd_name}")
        else:
            argv.append(cmd_name)

        def _process_node(node: Any, current_argv: list):
            if hasattr(node, "args"):
                for arg in node.args.argument:
                    if arg.name in mapped_kwargs:
                        val = mapped_kwargs[arg.name]
                        consumed_dests.add(arg.name)
                        if val is not None and val != "":
                            current_argv.append(str(val))

            if hasattr(node, "options"):
                for opt in node.options:
                    if isinstance(opt, Subcommand):
                        continue
                    dest = opt.dest
                    if dest in mapped_kwargs:
                        val = mapped_kwargs[dest]
                        consumed_dests.add(dest)
                        opt_name = name_overrides.get(dest, opt.name)
                        if isinstance(val, bool):
                            if val:
                                current_argv.append(opt_name)
                        elif val is not None and val != "":
                            current_argv.append(opt_name)
                            current_argv.append(str(val))

        _process_node(command, argv)

        for subcmd in command.options:
            if isinstance(subcmd, Subcommand):
                is_active = False
                dest = subcmd.dest
                if (
                    mapped_kwargs.get(dest) is True
                    or mapped_kwargs.get(subcmd.name) is True
                ):
                    is_active = True
                    consumed_dests.add(dest)
                    consumed_dests.add(subcmd.name)
                else:
                    for k, v in mapped_kwargs.items():
                        if str(v) == subcmd.name or str(v) == subcmd.dest:
                            is_active = True
                            consumed_dests.add(k)
                            break

                if is_active:
                    subcmd_name = name_overrides.get(dest, subcmd.name)
                    argv.append(subcmd_name)
                    _process_node(subcmd, argv)
                    break

        unconsumed_raw_keys = [
            raw_k for raw_k, dst in raw_key_to_dest.items() if dst not in consumed_dests
        ]
        return argv, unconsumed_raw_keys

    async def build_state(
        self, kwargs: dict[str, Any], bot: Bot, event: Event
    ) -> T_State:
        unconsumed_keys = []
        if self.command_formatter:
            try:
                if self.args_schema:
                    model_inst = model_validate(self.args_schema, kwargs)
                    argv = self.command_formatter(model_inst)
                else:
                    argv = self.command_formatter(kwargs)
            except Exception as e:
                logger.error(f"执行 command_formatter 失败: {e}")
                raise ToolRetryError(f"格式化参数失败: {e}")
        else:
            argv, unconsumed_keys = self._auto_infer_argv(kwargs)

        msg = UniMessage()
        for idx, item in enumerate(argv):
            if isinstance(item, str):
                msg += f" {item}" if idx > 0 else item
            else:
                if idx > 0:
                    msg += " "
                msg.append(item)

        command_func = getattr(self.matcher, "command", None)
        if not command_func:
            raise ToolRetryError("无法获取底层 Alconna 命令对象")
        command = cast(Alconna, command_func())
        if not command:
            raise ToolRetryError("底层 Alconna 命令对象为空")

        arp = command.parse(msg)
        if not arp.matched:
            error_info = (
                str(arp.error_info) if arp.error_info else "缺失必填参数或格式不匹配"
            )
            logger.warning(f"Alconna 解析失败: {error_info}. Argv: {argv}")
            schema_hint = (
                build_schema_hint(self.args_schema) if self.args_schema else ""
            )
            if unconsumed_keys:
                schema_hint += (
                    f"\n\n⚠️ [参数吸收警告] 字段 {unconsumed_keys} "
                    "未能被底层的 Alconna 命令树成功接收！\n"
                    "👉 这通常意味着 Schema 的字段名与实际命令的选项名(dest)不匹配。"
                )
            raise ToolRetryError(
                f"Alconna 内部解析失败: {error_info}。"
                f"请检查参数是否遗漏或名称对不齐。{schema_hint}"
            )

        cmd_result = CommandResult(result=arp, output=None)

        fake_state: T_State = {
            ALCONNA_RESULT: cmd_result,
            ALCONNA_EXEC_RESULT: {},
            **kwargs,
        }

        executor = getattr(self.matcher, "executor", None)
        if executor:
            selected = executor.select(bot, event)
            fake_state[ALCONNA_EXTENSION] = selected
            await selected.parse_wrapper(bot, fake_state, event, arp)

        logger.debug(f"通过真实解析引擎拉起 Alconna Matcher, Argv: {argv}")
        return fake_state


class NativeCommandAdapter(MatcherAdapter):
    """针对 NoneBot 原生 on_command 的适配器"""

    async def build_state(
        self, kwargs: dict[str, Any], bot: Bot, event: Event
    ) -> T_State:
        if self.command_formatter:
            try:
                if self.args_schema:
                    model_inst = model_validate(self.args_schema, kwargs)
                    arg_str = self.command_formatter(model_inst)
                else:
                    arg_str = self.command_formatter(kwargs)
            except Exception as e:
                logger.error(f"执行 command_formatter 失败: {e}")
                raise ToolRetryError(f"格式化参数失败: {e}")
            if isinstance(arg_str, list):
                arg_str = " ".join(str(i) for i in arg_str)
        else:
            arg_str = " ".join(
                str(v) for v in kwargs.values() if v is not None and str(v).strip()
            )

        try:
            msg_cls = type(event.get_message())
            msg_obj = msg_cls(arg_str)
        except Exception:
            try:
                from nonebot.adapters.onebot.v11 import Message as OB11Message

                msg_obj = OB11Message(arg_str)
            except Exception:
                msg_obj = arg_str

        fake_state: T_State = {
            "COMMAND": ("llm_bridge",),
            "_prefix": {
                "command_start": "/",
                "command": ("llm_bridge",),
                "command_arg": msg_obj,
            },
            **kwargs,
        }

        logger.debug(f"原生 Command 适配器注入 State _prefix.command_arg: {arg_str}")
        return fake_state


class MatcherTool(BaseTool):
    """
    通用状态机穿透工具。
    将原有的 nonebot 指令（on_alconna, on_command 等）
    无缝包装为大模型可调用的结构化工具。
    """

    def __init__(
        self,
        matcher: type[Matcher],
        adapter: MatcherAdapter,
        name: str,
        description: str,
        args_schema: type[BaseModel] | None,
        terminal: bool = True,
    ):
        """
        初始化通用状态机穿透工具。

        参数:
            matcher: 目标 Matcher 类，由 nonebot 装饰器创建的指令匹配器。
            adapter: 对应的 MatcherAdapter 桥接适配器，用于构建 Fake State。
            name: 工具的唯一标识名称（仅限英文字母、数字及下划线）。
            description: 工具的职责说明，指导大模型选用此工具。
            args_schema: 描述 LLM 需要填写的参数 Pydantic Schema，可以为 None。
            terminal: 是否为终端工具，若为 True 则执行完毕后立即中断大模型循环，默认 True。
        """  # noqa: E501
        self.matcher = matcher
        self.adapter = adapter
        self.terminal = terminal
        super().__init__(
            name=name,
            description=description,
            settings=ToolOptions(
                args_schema=args_schema,
                tags=["system:matcher_bridge"],
                metadata={
                    "source": "matcher_bridge",
                    "adapter_type": adapter.__class__.__name__,
                },
            ),
        )

    async def get_definition(
        self, context: RunContext | None = None
    ) -> ToolDefinition | None:
        tool_def = await super().get_definition(context)
        if tool_def:
            tool_def = self.adapter.modify_tool_definition(tool_def)
        return tool_def

    async def execute(
        self, context: RunContext | None = None, **kwargs: Any
    ) -> ToolResult:
        if not context:
            return ToolResult(
                output="缺少 RunContext 依赖，无法执行 Matcher"
            ).as_error()

        bot = context.get_bot()
        event = context.get_event()

        if not bot or not event:
            return ToolResult(
                output="❌ 当前处于无状态的自动化后台环境，缺乏真实的用户上下文(Event),"
                "严禁调用该原生平台指令工具！请换用其他方法解决。"
            ).as_error()

        try:
            fake_state = await self.adapter.build_state(kwargs, bot, event)
        except ToolRetryError as e:
            raise e
        except Exception as e:
            logger.error(f"构造状态失败: {e}", e=e)
            return ToolResult(output=f"适配器构造状态失败: {e}").as_error()

        logger.debug(
            f"拉起 Matcher: {self.name} (Adapter: {self.adapter.__class__.__name__})"
        )

        matcher_inst = self.matcher()

        try:
            await matcher_inst.run(bot, event, fake_state)

        except FinishedException:
            if self.terminal:
                return EndRunResult(output="任务已完成。结果已直接发送给用户。")
            return ToolResult(output="执行完毕，已直接向用户发送结果。")

        except PausedException:
            return ToolResult(
                output="执行被暂停（大模型不支持交互式Pause）。"
            ).as_error()
        except RejectedException:
            return ToolResult(output="执行被拒绝（参数缺失或错误）。").as_error()
        except Exception as e:

            def _unwrap_err(exc: BaseException) -> str:
                exceptions = getattr(exc, "exceptions", None)
                if exceptions is not None:
                    return " | ".join(_unwrap_err(inner) for inner in exceptions)
                return f"{type(exc).__name__}: {exc}"

            real_err = _unwrap_err(e)
            logger.error(
                f"Matcher '{self.name}' 穿透执行发生未捕获异常: {real_err}",
                e=e,
            )
            return ToolResult(output=f"执行时发生底层错误: {real_err}").as_error()

        if self.terminal:
            return EndRunResult(output="执行完毕。")

        return ToolResult(output="命令已在后台成功执行完成。")


def bind_matcher(
    matcher: type[Matcher],
    name: str,
    description: str,
    args_schema: type[BaseModel] | None = None,
    terminal: bool = True,
    auto_register: bool = True,
    command_formatter: Callable[[Any], list[Any] | str] | None = None,
    require_prefix: bool = True,
) -> MatcherTool:
    """
    将现有的 Nonebot Matcher (on_command, on_alconna等) 绑定并转化为大模型工具。
    使用策略模式(Adapter Pattern)自动推导 Matcher 的类型。

    参数:
        matcher: 目标 Matcher 对象 (由 on_command/on_alconna 返回的 Type[Matcher])
        name: 工具的名称 (仅限英文字母、数字及下划线)
        description: 工具的详细说明，大模型将根据此说明决定何时调用
        args_schema: 描述 LLM 需要填写的参数 Pydantic Schema。可以为 None
        terminal: 是否为"终端工具" (默认为 True，执行完毕后立即中断大模型思考循环)
        auto_register: 是否自动注册到系统的全局工具库中
        command_formatter: 可选的格式化器。对于原生 on_command，如果不传，
            则直接使用 kwargs values 拼接为空格字符串。
    """

    if require_prefix:
        ns = infer_plugin_namespace()
        if ns and ns not in ("global", "unknown"):
            if not name.startswith(f"{ns}_"):
                name = f"{ns}_{name}"

    is_alconna = (
        hasattr(matcher, "command")
        and callable(getattr(matcher, "command"))
        and hasattr(matcher, "executor")
    )

    if is_alconna:
        adapter = AlconnaAdapter(matcher, args_schema, command_formatter)
    else:
        adapter = NativeCommandAdapter(matcher, args_schema, command_formatter)

    tool_instance = MatcherTool(
        matcher=matcher,
        adapter=adapter,
        name=name,
        description=description,
        args_schema=args_schema,
        terminal=terminal,
    )

    if auto_register:
        from zhenxun.services.ai.tools.engine.registry import tool_provider_manager

        tool_provider_manager.register_tool(tool_instance)

    return tool_instance
