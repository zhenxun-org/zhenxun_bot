import asyncio
import csv
from pathlib import Path

from zhenxun.services.ai.context.rag.models import BaseRecord
from zhenxun.services.ai.utils.logger import log_knowledge as logger


class BaseReader:
    """读取器基类"""

    async def read(self, file_path: Path) -> BaseRecord | None:
        raise NotImplementedError


class TextReader(BaseReader):
    """处理 .txt, .md, .json 等纯文本"""

    async def read(self, file_path: Path) -> BaseRecord | None:
        try:
            import aiofiles

            async with aiofiles.open(file_path, encoding="utf-8", errors="ignore") as f:
                content = await f.read()

            enriched_content = f"文档名称：{file_path.stem}\n文档内容：\n{content}"
            return BaseRecord(
                content=enriched_content,
                metadata={
                    "name": file_path.name,
                    "extension": file_path.suffix.lower(),
                },
            )
        except Exception as e:
            logger.error(f"[TextReader] 读取文件失败 {file_path}: {e}")
            return None


class CSVReader(BaseReader):
    """
    处理 .csv 报表
    将其标准化为逗号分隔的文本块，便于后续的 RowChunking 处理。
    """

    async def read(self, file_path: Path) -> BaseRecord | None:
        try:

            def _read_csv():
                with open(file_path, encoding="utf-8", errors="ignore") as f:
                    reader = csv.reader(f)
                    return [
                        ",".join(
                            [
                                str(cell).replace("\n", " ").replace("\r", "")
                                for cell in row
                            ]
                        )
                        for row in reader
                    ]

            lines = await asyncio.to_thread(_read_csv)
            content = "\n".join(lines)

            enriched_content = f"数据表名称：{file_path.stem}\n数据内容：\n{content}"
            return BaseRecord(
                content=enriched_content,
                metadata={
                    "name": file_path.name,
                    "extension": file_path.suffix.lower(),
                },
            )
        except Exception as e:
            logger.error(f"[CSVReader] 读取 CSV 失败 {file_path}: {e}")
            return None
