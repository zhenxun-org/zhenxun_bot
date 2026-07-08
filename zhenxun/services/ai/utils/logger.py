"""
AI 模块专属日志代理门面
"""

from typing import Any

from zhenxun.services.log import logger as global_logger


class AILoggerProxy:
    def __init__(self, module_name: str, emoji: str = ""):
        self.module_name = module_name
        self.emoji = emoji
        self._cmd = f"AI|{self.module_name}"

    def _format(self, msg: str) -> str:
        """自动在消息开头追加 Emoji（如果消息本身不包含的话）"""
        if self.emoji and not str(msg).lstrip().startswith(self.emoji):
            return f"{self.emoji} {msg}"
        return msg

    def info(self, info: str, command: str | None = None, **kwargs: Any):
        cmd = command or self._cmd
        global_logger.info(self._format(info), command=cmd, **kwargs)

    def debug(self, info: str, command: str | None = None, **kwargs: Any):
        cmd = command or self._cmd
        global_logger.debug(self._format(info), command=cmd, **kwargs)

    def warning(self, info: str, command: str | None = None, **kwargs: Any):
        cmd = command or self._cmd
        global_logger.warning(self._format(info), command=cmd, **kwargs)

    def error(self, info: str, command: str | None = None, **kwargs: Any):
        cmd = command or self._cmd
        global_logger.error(self._format(info), command=cmd, **kwargs)

    def success(
        self,
        info: str,
        command: str | None = None,
        param: dict[str, Any] | None = None,
        result: str = "",
    ):
        cmd = command or self._cmd
        global_logger.success(
            self._format(info), command=cmd, param=param, result=result
        )

    def trace(self, info: str, command: str | None = None, **kwargs: Any):
        cmd = command or self._cmd
        global_logger.trace(self._format(info), command=cmd, **kwargs)


log_llm = AILoggerProxy("LLM")
log_agent = AILoggerProxy("Agent")
log_team = AILoggerProxy("Team")
log_tool = AILoggerProxy("Tool")
log_sandbox = AILoggerProxy("Sandbox")
log_memory = AILoggerProxy("Memory")
log_rag = AILoggerProxy("RAG")
log_flow = AILoggerProxy("Flow")
log_core = AILoggerProxy("Core")
log_knowledge = AILoggerProxy("Knowledge")
log_capability = AILoggerProxy("Capability")
