import os
from pathlib import Path
import re
from typing import Any

from zhenxun.services.ai.tools.core.decorators import tool
from zhenxun.services.ai.tools.models import ToolResult
from zhenxun.services.ai.utils.logger import log_knowledge as logger

from .base import BaseKnowledge


class FileSystemKnowledge(BaseKnowledge):
    """
    纯文本文件系统知识库。
    零依赖，无需向量数据库。通过大模型原生工具 (grep, list, read) 让其自主翻阅本地文件。
    """

    default_instructions = (
        "## 本地文件知识库\n"
        "你拥有访问本地专业文档的权限。在回答问题前，请遵循以下流程：\n"
        "1. **搜索**：优先使用 `search_knowledge_files` 通过关键词查找相关内容。\n"
        "2. **概览**：如果需要了解文件结构，使用 `list_knowledge_files`。\n"
        "3. **阅读**：找到目标后，使用 `read_knowledge_file` 获取完整上下文。\n"
        "**核心原则**：严禁凭空捏造事实，必须基于文件内容提取信息。"
    )

    def __init__(
        self,
        base_dir: str | Path,
        allowed_extensions: tuple[str, ...] | None = None,
        **kwargs: Any,
    ):
        """
        初始化文本文件系统知识库。

        参数:
            base_dir: 知识库对应的本地根目录路径。
            allowed_extensions: 允许读取和检索的文件后缀元组，
                默认支持 txt, md, json, csv, yaml, log。
            **kwargs: 透传给父类 BaseKnowledge 的额外参数。
        """
        super().__init__(**kwargs)
        self.base_dir = Path(base_dir).resolve()
        self.allowed_extensions = allowed_extensions or (
            ".txt",
            ".md",
            ".json",
            ".csv",
            ".yaml",
            ".log",
        )
        if not self.base_dir.exists():
            logger.warning(f"[FileSystemKnowledge] 警告：目录不存在 {self.base_dir}")
            self.base_dir.mkdir(parents=True, exist_ok=True)

    def _is_safe_path(self, target_path: Path) -> bool:
        """安全检查：防止跨目录访问"""
        try:
            return target_path.resolve().is_relative_to(self.base_dir)
        except Exception:
            return False

    @tool(
        name="search_knowledge_files",
        description="在知识库中搜索包含指定关键词的文件内容和上下文。",
    )
    async def search_knowledge_files(self, keyword: str) -> ToolResult:
        """扫描所有文本文件，返回包含关键词的行及上下文。"""
        results = []
        try:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        except Exception:
            return ToolResult(output=f"无效的搜索关键词: {keyword}").as_error()

        for root, _, files in os.walk(self.base_dir):
            for file in files:
                if not file.endswith(self.allowed_extensions):
                    continue

                file_path = Path(root) / file
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    lines = content.splitlines()

                    matches = []
                    for i, line in enumerate(lines):
                        if pattern.search(line):
                            start = max(0, i - 1)
                            end = min(len(lines), i + 2)
                            context = "\n".join(lines[start:end])
                            matches.append(context)

                    if matches:
                        rel_path = file_path.relative_to(self.base_dir).as_posix()
                        match_text = "\n---\n".join(matches[:5])
                        results.append(f"📁 文件: {rel_path}\n{match_text}")
                except Exception:
                    continue

        if not results:
            return ToolResult(output=f"未找到包含 '{keyword}' 的内容。尝试更换关键词。")

        final_output = "\n\n======\n\n".join(results[:10])
        return ToolResult(output=final_output)

    @tool(name="list_knowledge_files", description="列出知识库中所有可用的文档路径。")
    async def list_knowledge_files(self) -> ToolResult:
        files = []
        for root, _, filenames in os.walk(self.base_dir):
            for filename in filenames:
                file_path = Path(root) / filename
                rel_path = file_path.relative_to(self.base_dir).as_posix()
                files.append(rel_path)

        if not files:
            return ToolResult(output="知识库当前为空。")
        return ToolResult(output="可用文件列表:\n" + "\n".join(files))

    @tool(
        name="read_knowledge_file", description="读取知识库中指定文件的完整文本内容。"
    )
    async def read_knowledge_file(self, file_path: str) -> ToolResult:
        target = (self.base_dir / file_path).resolve()
        if not self._is_safe_path(target):
            return ToolResult(output="❌ 安全拦截：越权访问").as_error()
        if not target.is_file():
            return ToolResult(output=f"❌ 文件不存在: {file_path}").as_error()

        content = target.read_text(encoding="utf-8", errors="ignore")
        return ToolResult(output=content)
