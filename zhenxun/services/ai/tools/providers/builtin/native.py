from zhenxun.services.ai.tools.core.tool import ServerSideTool


class WebSearchTool(ServerSideTool):
    """原生网页搜索工具"""

    type_id = "web_search"

    def __init__(
        self,
        name: str = "google_search",
        description: str = "使用 Google 搜索查找实时信息。",
        dynamic_threshold: float | None = None,
        domain_filters: list[str] | None = None,
    ):
        super().__init__(name=name, description=description)
        self.dynamic_threshold = dynamic_threshold
        self.domain_filters = domain_filters


class CodeExecutionTool(ServerSideTool):
    """原生代码沙箱执行工具"""

    type_id = "code_execution"

    def __init__(self, name: str = "code_execution", timeout: int | None = None):
        super().__init__(
            name=name, description="执行 Python 代码来解决数学或数据问题。"
        )
        self.timeout = timeout


class XSearchTool(ServerSideTool):
    """原生 X (推特) 搜索工具 (Grok独占)"""

    type_id = "x_search"

    def __init__(
        self,
        allowed_x_handles: list[str] | None = None,
        excluded_x_handles: list[str] | None = None,
    ):
        super().__init__(
            name="x_search", description="在 X (Twitter) 上搜索推文、用户资料和时间线。"
        )
        self.allowed_x_handles = allowed_x_handles
        self.excluded_x_handles = excluded_x_handles


class ComputerUseTool(ServerSideTool):
    """原生的桌面环境控制工具"""

    type_id = "computer_use"

    def __init__(self, display_width_px: int = 1024, display_height_px: int = 768):
        super().__init__(name="computer_use", description="控制电脑用户界面。")
        self.display_width_px = display_width_px
        self.display_height_px = display_height_px


class FileSearchTool(ServerSideTool):
    """原生文件检索工具"""

    type_id = "file_search"

    def __init__(self, name: str = "file_search"):
        super().__init__(name=name, description="搜索已索引的文件。")


class GoogleMapsTool(ServerSideTool):
    """原生谷歌地图检索工具"""

    type_id = "google_map"

    def __init__(self):
        super().__init__(name="google_map", description="搜索 Google 地图数据。")


class UrlContextTool(ServerSideTool):
    """原生 URL 上下文拉取工具"""

    type_id = "url_context"

    def __init__(self):
        super().__init__(name="url_context", description="从指定 URL 获取上下文信息。")


class Native:
    """
    云端原生工具命名空间工厂
    """

    @classmethod
    def web_search(
        cls,
        name: str = "google_search",
        description: str = "使用 Google 搜索查找实时信息。",
        dynamic_threshold: float | None = None,
        domain_filters: list[str] | None = None,
    ) -> WebSearchTool:
        """
        原生网页搜索引擎工具 (如 Google Search, Bing)。
        """
        return WebSearchTool(name, description, dynamic_threshold, domain_filters)

    @classmethod
    def x_search(
        cls,
        allowed_x_handles: list[str] | None = None,
        excluded_x_handles: list[str] | None = None,
    ) -> XSearchTool:
        """
        原生 X (Twitter) 检索工具 (Grok 独占)。
        """
        return XSearchTool(allowed_x_handles, excluded_x_handles)

    @classmethod
    def code_execution(
        cls, name: str = "code_execution", timeout: int | None = None
    ) -> CodeExecutionTool:
        """
        原生代码沙箱执行工具 (如 Gemini Code Execution, OpenAI Advanced Data Analysis)。
        """
        return CodeExecutionTool(name, timeout)

    @classmethod
    def computer_use(
        cls, display_width_px: int = 1024, display_height_px: int = 768
    ) -> ComputerUseTool:
        """
        原生桌面环境控制工具 (如 Claude Computer Use)。
        """
        return ComputerUseTool(display_width_px, display_height_px)

    @classmethod
    def file_search(cls, name: str = "file_search") -> FileSearchTool:
        """
        原生文件检索工具。
        """
        return FileSearchTool(name)

    @classmethod
    def google_map(cls) -> GoogleMapsTool:
        """
        原生谷歌地图检索工具 (Gemini 独占)。
        """
        return GoogleMapsTool()

    @classmethod
    def url_context(cls) -> UrlContextTool:
        """
        原生 URL 上下文拉取工具。
        """
        return UrlContextTool()
