from typing import Any, cast

from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.run.di import Inject
from zhenxun.services.ai.sandbox.models import SandboxBlueprint
from zhenxun.services.ai.sandbox.protocols import (
    SupportsCommandExecution,
    SupportsFileSystem,
)
from zhenxun.services.ai.tools.core.decorators import Rules, tool
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import ResolvedToolPayload, ToolResult
from zhenxun.services.ai.tools.providers.skills.manager import (
    skill_env_manager,
    skill_manager,
)
from zhenxun.services.ai.tools.providers.skills.models import Skill
from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_copy
from zhenxun.utils.utils import infer_plugin_namespace


class SkillSandboxExecutionMixin:
    """技能沙箱执行与自愈逻辑混入类"""

    async def _ensure_skill_workspace(
        self, skill: Skill, session_id: str, sandbox: Any
    ) -> tuple[Any, str]:
        """负责环境隔离与沙箱启动装配，返回底层执行器和目标工作区路径"""
        bp = model_copy(skill.frontmatter.blueprint, deep=True)
        bp.enable_network = skill.frontmatter.enable_network

        executor = await sandbox.get_or_create_session(session_id, blueprint=bp)
        fs_executor = cast(SupportsFileSystem, executor)

        target_workspace = f"{executor.workspace_path}/{skill.id}"

        if skill.id not in executor.loaded_skills:
            await fs_executor.upload_raw_dir(str(skill.path), target_workspace)
            try:
                await sandbox.setup_workspace_environment(session_id, target_workspace)
            except Exception as e:
                logger.warning(f"设置沙箱工作区环境失败: {e}")
            executor.loaded_skills.add(skill.id)

        return executor, target_workspace

    def _prepare_skill_env_vars(
        self, skill: Skill, context: RunContext | None, **kwargs
    ) -> dict[str, str] | ToolResult:
        """处理环境变量注入与缺失拦截"""
        configured_envs = skill_env_manager.get_envs_for_skill(
            skill.namespace, skill.id
        )
        missing_keys = [
            k for k in skill.frontmatter.required_envs if not configured_envs.get(k)
        ]
        if missing_keys:
            logger.warning(f"技能 {skill.id} 因缺少环境变量 {missing_keys} 被拦截。")
            return ToolResult(
                output=(
                    f"❌ 技能执行被系统拦截：缺少必需的全局环境变量 "
                    f"{missing_keys}。\n"
                    "💡 [智能体自愈引导]：当前技能的底层配置缺失，无法正常运行。"
                    "请你立即停止尝试，并向用户抱歉，提示用户（或 Bot 管理员）"
                    "在机器人后端的 `data/ai/skill_envs.json` 文件中为该技能配置"
                    "相应的环境变量（API Key 等），配置完成后方可使用。"
                ),
            ).as_error()

        env_vars = {}
        for k, v in configured_envs.items():
            if v:
                env_vars[k] = str(v)

        for k, v in kwargs.items():
            env_vars[k] = str(v)
        if context:
            for k, v in context.state.items():
                if isinstance(v, str | int | float | bool):
                    env_vars[k] = str(v)

        final_env = {}
        for k, v in env_vars.items():
            final_env[k] = str(v)
            final_env[k.upper()] = str(v)

        return final_env

    def _format_execution_result(self, result: Any, command: str) -> ToolResult:
        """统一处理沙箱输出并转换为 ToolResult"""
        output = (
            result.stdout + ("\nSTDERR:\n" + result.stderr if result.stderr else "")
        ).strip()

        if getattr(result, "is_timeout", False) or result.exit_code == -1:
            final_output = (
                f"🚨 终端命令执行发生严重系统异常或超时被强杀\n"
                f"(Exit Code: {result.exit_code})！\n"
                "这通常意味着网络不通、下载数据过大耗时太长，"
                "或沙箱环境崩溃。输出为空。"
            )
        elif result.exit_code != 0:
            final_output = (
                f"❌ 终端命令执行失败 (Exit Code: {result.exit_code})。\n"
                f"输出日志:\n{output or '无日志输出'}"
            )
        else:
            final_output = output or "✅ 执行成功 (无控制台输出)。"

        logger.info(f"终端命令 {command} 执行完毕, Exit Code: {result.exit_code}")
        tool_result = ToolResult(output=final_output)
        if result.exit_code != 0:
            tool_result = tool_result.as_error()
        return tool_result

    async def _execute_skill_command_in_sandbox(
        self,
        skill: Skill,
        command: str,
        context: RunContext | None,
        sandbox: Any,
        **kwargs,
    ) -> ToolResult:
        """主调度方法"""
        session_id = (
            context.session_id
            if context and context.session_id
            else f"skill_{skill.id}_session"
        )

        executor, target_workspace = await self._ensure_skill_workspace(
            skill, session_id, sandbox
        )

        env_res = self._prepare_skill_env_vars(skill, context, **kwargs)
        if isinstance(env_res, ToolResult):
            return env_res

        env_res["SKILL_DIR"] = target_workspace

        cmd_executor = cast(SupportsCommandExecution, executor)
        result = await cmd_executor.run_process(
            command, cwd=target_workspace, timeout=180, env=env_res
        )

        return self._format_execution_result(result, command)


class SkillMetaToolkit(BaseToolkit, SkillSandboxExecutionMixin):
    """动态发现模式。提供通用的元工具，供大模型按需加载和执行任意可用技能。"""

    default_prefix = ""

    def __init__(self, allowed_skills: list[Skill] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._allowed_skills = allowed_skills
        if self._allowed_skills is not None:
            self._instance_instructions = (
                (self._instance_instructions or self.default_instructions)
                + "\n\n🚨 **[系统安全提示]**：当前运行在严格沙盒隔离模式，"
                "你只能访问特定被授权的技能。"
            )

    async def _get_skill(self, skill_name: str) -> Skill | None:
        """按需从隔离白名单或全局管理器中获取技能"""
        if self._allowed_skills is not None:
            for s in self._allowed_skills:
                if s.id == skill_name or s.name == skill_name:
                    return s
            return None

        return await skill_manager.get_skill_details(
            skill_name, namespace=infer_plugin_namespace()
        )

    async def resolve(self, context: RunContext | None = None) -> ResolvedToolPayload:
        payload = await super().resolve(context)
        if self._allowed_skills is not None:
            catalog_parts = []
            for skill in self._allowed_skills:
                catalog_parts.append(
                    f"  <skill>\n    <name>{skill.id}</name>\n"
                    f"    <description>{skill.description}</description>\n  </skill>"
                )
            if catalog_parts:
                catalog_xml = (
                    "<available_skills>\n"
                    + "\n".join(catalog_parts)
                    + "\n</available_skills>"
                )
                payload.injected_prompts.append(
                    f"--- 当前受限的可用技能库 ---\n\n{catalog_xml}"
                )
        return payload

    default_instructions = """\
## 技能元工具系统
你可以通过此工具箱动态加载和执行外部技能。工作流如下：
1. 使用 `read_skill_instructions` 传入技能名称，获取该技能的完整指南。
2. 系统将返回严谨的 `<skill>` XML 节点树，请仔细阅读 `<instructions>` 了解业务规则。
3. **[重点] 环境装配与执行规范**：
   - **环境变量**：系统已自动将该技能的物理根目录注入为环境变量 `$SKILL_DIR`，在执行终端命令时可直接使用它来定位文件（例如 `cat $SKILL_DIR/package.json`）。
   - **按需安装(懒加载)**：沙箱内通常已通过底层 Blueprint 预装了所需的依赖包。**即使技能指南(说明文档)中写了“安装依赖”的步骤，你也必须忽略它！** 严禁在没有任何报错的情况下主动去执行安装命令（如 `npm install`, `pip install`）。
   - **脚本执行**：若指南中要求执行物理存在的脚本文件（如 `<available_scripts>` 节点列出的文件），请统一调用 `execute_skill_command` 并加上解释器和完整路径（如 `python3 $SKILL_DIR/scripts/xxx.py`）。
   - **终端执行**：若指南中提供的是纯命令行终端指令（例如 `curl`, `infsh`, `gh` 等），请调用 `execute_skill_command` 在技能专属沙箱中直接执行该命令。
   - **智能自愈 (Agentic Healing)**：如果执行脚本或命令时失败（如 Exit Code 非 0），你必须自主阅读输出日志 (Stderr/Stdout)，分析报错原因。
     - **只有当运行报错且明确提示缺少依赖时**（如 `command not found`, `ModuleNotFoundError` 等），你才可以调用 `execute_skill_command` 安装缺失的依赖（Python 依赖强烈建议使用 `uv pip install <pkg>`，Node 使用 `npm install <pkg>`），安装成功后再次重试。
     - 如果是其他错误，请结合技能指南调整参数或操作流程后重试。
4. 如需阅读参考文档，请参考 `<available_references>` 节点并调用 `read_skill_file`。"""  # noqa: E501

    @tool(
        name="read_skill_instructions",
        description="加载指定技能的完整使用说明与可用资源清单。返回值为 XML 结构。",
        rules=[Rules.silent()],
    )
    async def read_skill_instructions(self, skill_name: str) -> ToolResult:
        skill = await self._get_skill(skill_name)
        if not skill:
            return ToolResult(output=f"越权操作或未找到技能: {skill_name}").as_error()

        res = skill.to_xml()
        res += (
            "\n\n**提示**: 你可以使用 `read_skill_file` 工具读取该技能"
            "目录下的任何附加文件（如 references/ 或 templates/ 下的文档）。"
        )
        logger.info(f"已加载技能 {skill.name} 指南。")
        return ToolResult(output=res)

    @tool(
        name="execute_skill_command",
        description=(
            "在技能专属的安全沙箱内执行任意终端命令行指令"
            "（如 curl, npm, gh 等）。仅当技能指南中提供的是终端命令"
            "而非特定脚本文件时使用。"
        ),
    )
    async def execute_skill_command(
        self,
        skill_name: str,
        command: str,
        context: RunContext | None = None,
        sandbox: Inject.Sandbox = None,
        **kwargs,
    ) -> ToolResult:
        skill = await self._get_skill(skill_name)
        if not skill:
            return ToolResult(
                output=f"越权操作或未找到技能: {skill_name}",
            ).as_error()

        if sandbox is None and context is not None:
            sandbox = Inject._providers["sandbox"]["global"](context)

        return await self._execute_skill_command_in_sandbox(
            skill, command, context, sandbox, **kwargs
        )

    @tool(
        name="read_skill_file",
        description=(
            "安全读取指定技能目录下的附加文件"
            "（如 references/ 里的参考文档或 scripts/ 里的代码）。"
        ),
        rules=[Rules.silent()],
    )
    async def read_skill_file(
        self,
        skill_name: str,
        file_path: str,
        context: RunContext | None = None,
        sandbox: Inject.Sandbox = None,
    ) -> ToolResult:
        skill = await self._get_skill(skill_name)
        if not skill:
            return ToolResult(output=f"越权操作或未找到技能: {skill_name}").as_error()

        content = await skill_manager.read_skill_resource(skill, file_path)

        if content is not None:
            logger.info(
                f"已从本地读取文件 {file_path} (共 {len(content)} 字符)。"
                f"内容摘要: {content[:100]}..."
            )
            return ToolResult(output=content)

        session_id = (
            context.session_id
            if context and context.session_id
            else f"skill_{skill.id}_session"
        )
        bp = SandboxBlueprint(enable_network=skill.frontmatter.enable_network)
        try:
            if sandbox is None and context is not None:
                sandbox = Inject._providers["sandbox"]["global"](context)

            executor = await sandbox.get_or_create_session(session_id, blueprint=bp)
            fs_executor = cast(SupportsFileSystem, executor)

            clean_file_path = file_path.lstrip("/")
            sandbox_target_path = (
                f"{executor.workspace_path}/{skill.id}/{clean_file_path}"
            )

            content = await fs_executor.read_raw_file(sandbox_target_path)

            if content.startswith("Error: File") or content.startswith("Failed to"):
                return ToolResult(
                    output=(
                        f"❌ 找不到文件: {file_path} "
                        f"(Provider {skill.source} 与沙箱中均未找到)"
                    ),
                ).as_error()

            logger.info(
                "已从沙箱读取动态生成的文件 "
                f"{sandbox_target_path} (共 {len(content)} 字符)。"
            )
            return ToolResult(output=content)
        except Exception as e:
            return ToolResult(output=f"❌ 尝试读取沙箱文件时发生异常: {e}").as_error()
