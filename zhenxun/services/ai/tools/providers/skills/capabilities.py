from collections.abc import Sequence
from pathlib import Path
from typing import Any

from zhenxun.services.ai.capabilities import AbstractCapability
from zhenxun.services.ai.run.context import RunContext
from zhenxun.utils.utils import infer_plugin_namespace

from .manager import skill_manager
from .models import Skill, SkillSource
from .toolkit import SkillMetaToolkit


class SkillCapability(AbstractCapability):
    """技能库挂载能力组件"""

    def __init__(
        self,
        skills: Sequence[str | Path | Skill | SkillSource] | None = None,
        namespace: str | None = None,
    ):
        """
        初始化技能库挂载能力组件。

        参数:
            skills: 需要挂载到该环境下的技能列表，支持名称、路径、Skill 实例或 SkillSource。
            namespace: 该技能库所处的作用域命名空间，若不指定则自动推导。
        """  # noqa: E501
        self.skills = skills or []
        self.namespace = namespace or infer_plugin_namespace()

    async def get_tools(self, context: RunContext) -> list[Any]:
        tools = []

        if self.skills:
            resolved_skills = await skill_manager.resolve_mixed_skills(
                self.skills, namespace=self.namespace
            )
            tools.append(SkillMetaToolkit(allowed_skills=resolved_skills))

        return tools
