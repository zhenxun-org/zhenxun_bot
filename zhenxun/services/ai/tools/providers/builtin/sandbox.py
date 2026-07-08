import asyncio
from typing import Any

from zhenxun.services.ai.core.stream_events import ToolStreamChunkEvent, UserCustomEvent
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.run.di import Inject
from zhenxun.services.ai.sandbox.models import (
    SandboxBlueprint,
)
from zhenxun.services.ai.tools.core.decorators import Rules, tool
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import ToolResult
from zhenxun.services.ai.utils.logger import log_tool as logger
from zhenxun.utils.pydantic_compat import model_copy


class SandboxToolkit(BaseToolkit):
    default_prefix = ""

    default_instructions = (
        "## 🖥️ 沙箱工作区交互规范\n"
        "你拥有物理隔离的沙箱环境。请严格遵循以下调度规则：\n"
        "1. **短时/非交互任务**：直接使用 `execute_code`（如数据计算、算法运行）。"
        "⚠️ 该工具**严禁包含 `input()`** 等阻塞式交互。\n"
        "2. **长驻/交互式任务**：若需运行 Web Server 或含 `input()` 的交互程序，"
        "**必须**：\n"
        "   - 先用 `write_sandbox_file` 将代码保存至当前工作区。\n"
        "   - 再用 `execute_terminal_command(is_interactive=True)` 启动并挂起进程。\n"
        "   - 通过 `send_sandbox_input` / `read_sandbox_screen` 与屏幕画面交互。\n"
        "3. **终端互斥锁**：虚拟终端只能单线程运行前台程序。"
        "若程序报错或死循环卡死，**必须**先调用 `interrupt_sandbox` "
        "打断它，方可进行后续修改。"
    )

    def __init__(
        self,
        blueprint: SandboxBlueprint | None = None,
        sandbox_session_id: str | None = None,
        **kwargs: Any,
    ):
        """
        初始化沙箱工作区工具箱。

        参数：
            blueprint: 沙箱蓝图配置对象，包含沙箱运行时的各种参数定义（例如是否需要状态、端口映射等）。
            sandbox_session_id: 显式指定的沙箱会话 ID。如果不指定，则自动与当前执行上下文绑定。
            kwargs: 其他透传给 BaseToolkit 的参数。
        """  # noqa: E501
        super().__init__(**kwargs)
        self.blueprint = blueprint or SandboxBlueprint()

        self.sandbox_session_id = sandbox_session_id

    async def _get_session(self, context: RunContext, sandbox: Any) -> Any:
        """获取或创建沙箱会话实例"""
        session_id = (
            self.sandbox_session_id or context.session_id or "default_sandbox_session"
        )
        return await sandbox.get_or_create_session(session_id, self.blueprint)

    def _get_pty(self, context: RunContext) -> Any:
        """从上下文获取虚拟终端"""
        session_id = self.sandbox_session_id or context.session_id or "default"
        return context.session.shared_state.get(f"pty_{session_id}")

    @tool(
        name="execute_code",
        description=(
            "在沙箱环境中执行代码。\n"
            "支持多语言执行，请在 language 参数中指定具体的编程语言"
            "（如 python, bash 等）。\n"
            "默认超时时间为 45 秒。如果你的代码需要更长的时间或等待用户输入，"
            "它将被挂在后台运行。\n"
            "你将收到目前的屏幕输出，并可以决定后续操作。"
        ),
    )
    async def execute_code(
        self,
        code: str,
        language: str,
        context: RunContext,
        sandbox: Inject.Sandbox,
    ) -> ToolResult:
        session_id = (
            self.sandbox_session_id or context.session_id or "default_sandbox_session"
        )
        logger.info(
            f"大模型请求执行 {language} 代码 (Session: {session_id}, "
            f"长度: {len(code)} 字符)"
        )

        from zhenxun.services.ai.sandbox.runtimes import CodeExecutorRegistry

        ns = getattr(context.session, "namespace", "global") if context else "global"
        supported = CodeExecutorRegistry.get_supported_languages(ns)
        if CodeExecutorRegistry._normalize_lang(language) not in supported:
            from zhenxun.services.ai.core.exceptions import ToolRetryError

            raise ToolRetryError(
                f"当前沙箱不支持该语言 '{language}'。支持的语言有: {supported}。"
                "请换用支持的语言重新编写代码！"
            )

        bp = model_copy(self.blueprint, deep=True)

        if context:
            await context.run.emit(
                ToolStreamChunkEvent(
                    tool_name="Sandbox", content="正在分析代码依赖并分配沙箱环境..."
                )
            )
        executor = await self._get_session(context, sandbox)

        state_key = f"code_exec_{session_id}_{language}"
        code_executor = context.session.shared_state.get(state_key)
        if code_executor and getattr(code_executor, "session", None) is not executor:
            code_executor = None

        if not code_executor:
            code_executor = CodeExecutorRegistry.create_executor(
                language, bp.needs_state, executor, namespace=ns
            )
            context.session.shared_state[state_key] = code_executor

        if context:
            await context.run.emit(
                ToolStreamChunkEvent(
                    tool_name="Sandbox",
                    content=f"沙箱已就绪，正在后台执行 {language} 代码...",
                )
            )

        import re

        clean_code = code.strip()
        match = re.search(
            r"^```[a-zA-Z0-9_-]*\r?\n(.*?)\r?\n```$",
            clean_code,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            clean_code = match.group(1)

        line_buffer = ""

        async def _stream_output(stream_type: str, data: bytes):
            nonlocal line_buffer
            line_buffer += data.decode("utf-8", errors="replace")

        try:
            result = await code_executor.execute_code(
                code=clean_code,
                timeout=45,
                on_output=_stream_output,
            )
        except Exception as e:
            logger.error(f"沙箱执行框架异常: {e}")
            from zhenxun.services.ai.core.exceptions import AbortException

            raise AbortException(
                reason=f"System Error: 容器执行环境不可用，异常信息: {e}",
                display=f"❌ 沙箱框架异常: {e}",
            )

        output_parts = []
        if result.exit_code != 0 or result.stderr:
            output_parts.append(f"Exit Code: {result.exit_code}")

        if result.stdout:
            output_parts.append(f"Stdout:\n{result.stdout.strip()[:2000]}")

        if result.stderr:
            output_parts.append(f"Stderr:\n{result.stderr.strip()[:2000]}")

        if result.error:
            output_parts.append(f"System Error:\n{result.error}")

        output_text = "\n".join(output_parts)

        system_notice = ""
        if result.is_timeout:
            system_notice = (
                "\n\n⚠️ 警告: 代码执行超时！进程仍在后台挂起，"
                "若为死循环请立刻使用 `interrupt_sandbox`。"
            )
        if result.stderr and "StdinNotImplementedError" in result.stderr:
            system_notice += (
                "\n\n🚨 致命错误: 当前环境不支持 input()。"
                "请将代码写入文件并通过 "
                "execute_terminal_command(is_interactive=True) 运行！"
            )

        image_bytes_list = []

        if getattr(result, "images", None):
            import base64

            for b64_str in result.images:
                try:
                    image_bytes_list.append(base64.b64decode(b64_str))
                except Exception:
                    pass

        if getattr(result, "artifacts", None):
            for filename, file_bytes in result.artifacts.items():
                if filename.endswith((".png", ".jpg", ".jpeg")):
                    image_bytes_list.append(file_bytes)

        from zhenxun.services.ai.core.messages import (
            ImagePart,
            LLMContentPart,
            TextPart,
        )

        final_output: list[LLMContentPart] = [TextPart(text=output_text.strip())]

        for img_bytes in image_bytes_list:
            final_output.append(ImagePart(raw=img_bytes))

        final_output_text = output_text.strip()
        if system_notice:
            final_output_text += f"\n\n{system_notice}"
        final_output[0] = TextPart(text=final_output_text)

        result = ToolResult(
            output=final_output if len(final_output) > 1 else final_output_text
        )
        if len(image_bytes_list) > 0 and context:
            await context.run.emit(UserCustomEvent(display=final_output))
        return result

    @tool(
        name="execute_terminal_command",
        description=(
            "在沙箱的终端中执行 Shell 命令"
            "（例如 `python3 script.py` 或 `npm start`）。\n"
            "如果只是执行普通的短时非交互脚本，"
            "保持 is_interactive=False 即可（执行速度极快且稳定）。\n"
            "如果程序包含 `input()` 或需要长期驻留（如 Server），"
            "请务必设置 is_interactive=True 开启虚拟屏幕模式！"
        ),
    )
    async def execute_terminal_command(
        self,
        command: str,
        context: RunContext,
        sandbox: Inject.Sandbox,
        is_interactive: bool = False,
    ) -> ToolResult:
        session_id = self.sandbox_session_id or context.session_id or "default"

        if context:
            await context.run.emit(
                ToolStreamChunkEvent(
                    tool_name="Sandbox", content=f"正在虚拟终端执行命令: {command} ..."
                )
            )
        executor = await self._get_session(context, sandbox)

        if not is_interactive:
            res = await executor.run_process(command)

            if getattr(res, "is_timeout", False):
                return ToolResult(
                    output=(
                        "⚠️ 警告: 命令执行超时！若程序需常驻或等待输入，"
                        f"请务必设置 is_interactive=True。\n"
                        f"Stdout: {res.stdout}\nStderr: {res.stderr}"
                    )
                ).as_error()

            return ToolResult(
                output=(
                    f"Exit Code: {res.exit_code}\n"
                    f"Stdout: {res.stdout}\n"
                    f"Stderr: {res.stderr}"
                )
            )

        pty = self._get_pty(context)
        if pty:
            await pty.close()

        interactive_session = await executor.create_pty_session()
        context.session.shared_state[f"pty_{session_id}"] = interactive_session

        try:
            await interactive_session.start(command)
            await asyncio.sleep(1.5)
            screen = await interactive_session.read_output()
            return ToolResult(
                output=f"📺 虚拟终端已启动，屏幕快照:\n```text\n{screen}\n```"
            )
        except Exception as e:
            return ToolResult(output=f"虚拟屏幕启动异常: {e}").as_error()

    @tool(
        name="send_sandbox_input",
        description=(
            "向当前沙箱中正在挂起运行的后台进程"
            "（如等待 input() 的 Python 脚本）发送输入文本。\n"
            "注意：你需要自己在文本末尾加上换行符 \\n 来模拟回车键。"
        ),
    )
    async def send_sandbox_input(self, text: str, context: RunContext) -> ToolResult:
        interactive_session = self._get_pty(context)
        if not interactive_session:
            return ToolResult(
                output=(
                    "错误：没有运行中的交互式虚拟屏幕。"
                    "请先调用 execute_terminal_command(is_interactive=True)。"
                )
            ).as_error()

        text = text.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")

        await interactive_session.send_input(text)

        await asyncio.sleep(1.5)
        output = await interactive_session.read_output(timeout=5)

        if context:
            await context.run.emit(
                ToolStreamChunkEvent(
                    tool_name=context.call.tool_name, content="⌨️ 已向后台进程发送输入"
                )
            )
        return ToolResult(
            output=f"已发送按键。📺 屏幕刷新后快照如下：\n```text\n{output}\n```"
        )

    @tool(
        name="read_sandbox_screen",
        description=(
            "主动窥探并读取当前虚拟屏幕的画面。当你觉得后台程序可能已经渲染出新内容时，可以使用此工具。"
        ),
    )
    async def read_sandbox_screen(self, context: RunContext) -> ToolResult:
        interactive_session = self._get_pty(context)
        if not interactive_session:
            return ToolResult(output="没有运行中的虚拟屏幕。").as_error()

        output = await interactive_session.read_output()
        return ToolResult(output=f"📺 当前屏幕快照:\n```text\n{output}\n```")

    @tool(
        name="interrupt_sandbox",
        description=(
            "向当前沙箱发送 Ctrl+C (SIGINT) 信号，强制中断正在死循环或挂起的后台进程。"
        ),
    )
    async def interrupt_sandbox(self, context: RunContext) -> ToolResult:
        interactive_session = self._get_pty(context)
        if not interactive_session:
            return ToolResult(output="没有运行中的虚拟屏幕需要中断。").as_error()

        await interactive_session.interrupt()
        await asyncio.sleep(1)
        output = await interactive_session.read_output()
        if context:
            await context.run.emit(
                ToolStreamChunkEvent(
                    tool_name=context.call.tool_name, content="🛑 已强制中断后台进程"
                )
            )
        return ToolResult(
            output="✅ 成功发送 Ctrl+C 中断信号。"
            f"📺 当前屏幕快照：\n```text\n{output}\n```"
        )

    @tool(
        name="write_sandbox_file",
        description="将文本内容写入沙箱文件系统中，支持保存大块数据或配置，避免超过对话上下文。",
        rules=[Rules.silent()],
    )
    async def write_sandbox_file(
        self, path: str, content: str, context: RunContext, sandbox: Inject.Sandbox
    ) -> ToolResult:
        executor = await self._get_session(context, sandbox)

        success = await executor.write(path, content.encode("utf-8"))
        if success:
            logger.info(f"📝 已向沙箱写入文件: {path}")
            return ToolResult(output=f"成功将内容写入文件: {path}")
        else:
            from zhenxun.services.ai.core.exceptions import AbortException

            raise AbortException(
                reason="写入文件失败 (当前沙箱环境失联或不支持持久化IO)",
                display="❌ 写入文件失败：沙箱失联",
            )

    @tool(
        name="read_sandbox_file",
        description="从沙箱文件系统中读取指定文件的文本内容。",
        rules=[Rules.silent()],
    )
    async def read_sandbox_file(
        self, path: str, context: RunContext, sandbox: Inject.Sandbox
    ) -> ToolResult:
        executor = await self._get_session(context, sandbox)

        try:
            content_bytes = await executor.read(path)
            content = content_bytes.decode("utf-8", errors="replace")
            if content.startswith("Error:") or content.startswith("Failed to"):
                return ToolResult(output=content).as_error()
            logger.info(f"已读取沙箱文件 {path} (共 {len(content)} 字符)")
            return ToolResult(output=content)
        except Exception as e:
            from zhenxun.services.ai.core.exceptions import AbortException

            raise AbortException(reason=f"读取文件发生框架级异常: {e}")
