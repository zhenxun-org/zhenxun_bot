from abc import ABC, abstractmethod
import asyncio
from collections.abc import Sequence
import json
from pathlib import Path
import re
from typing import Any, cast
from typing_extensions import Self

import aiofiles
import yaml

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.ai.utils.logger import log_tool as logger
from zhenxun.utils.pydantic_compat import model_dump, model_validate
from zhenxun.utils.utils import infer_plugin_namespace

from .models import (
    INSTRUCTIONS,
    METADATA,
    RESOURCES,
    Skill,
    SkillBlueprintBuilder,
    SkillEnvConfig,
    SkillFrontmatter,
)


class SkillConfigManager:
    """基于 Skill ID 隔离的持久化配置金库管理器"""

    def __init__(self):
        self.config_path = DATA_PATH / "ai" / "skill_envs.json"
        self.config = SkillEnvConfig()
        self._load_sync()

    def _load_sync(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    self.config = model_validate(SkillEnvConfig, json.load(f))
            except Exception as e:
                logger.error(f"加载 skill_envs.json 失败: {e}")

    async def save(self):
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self.config_path, "w", encoding="utf-8") as f:
                content = json.dumps(
                    model_dump(self.config), ensure_ascii=False, indent=4
                )
                await f.write(content)
        except Exception as e:
            logger.error(f"保存 skill_envs.json 失败: {e}")

    def get_envs_for_skill(self, namespace: str, skill_id: str) -> dict[str, str]:
        return self.config.envs.get(namespace, {}).get(skill_id, {})

    async def ensure_template(
        self, namespace: str, skill_id: str, required_envs: list[str]
    ):
        """确保配置文件中存在所需的模板，缺失则补充为空字符串"""
        if not required_envs:
            return

        changed = False
        skill_envs = self.config.envs.setdefault(namespace, {}).setdefault(skill_id, {})
        for env_key in required_envs:
            if env_key not in skill_envs:
                skill_envs[env_key] = ""
                changed = True

        if changed:
            await self.save()


skill_env_manager = SkillConfigManager()


class SkillManager:
    """全局技能注册与发现中心 (本地文件系统模式)"""

    _instance: "SkillManager | None" = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cast(Self, cls._instance)

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._skills: dict[str, Skill] = {}
        self._scan_dirs: dict[Path, str] = {}
        self._initialized = True
        self._discovery_lock = asyncio.Lock()

        default_skill_dir = DATA_PATH / "ai" / "skills"
        default_skill_dir.mkdir(parents=True, exist_ok=True)
        self._scan_dirs[default_skill_dir] = "global"

    def add_scan_dir(self, path: str | Path, namespace: str | None = None):
        p = Path(path).resolve()
        if namespace is None:
            namespace = infer_plugin_namespace()
        if p not in self._scan_dirs:
            self._scan_dirs[p] = namespace

    def _get_valid_skill_dir(self, path: str | Path) -> Path | None:
        """统一目录校验：判断是否为合法的技能目录"""
        p = Path(path).resolve()
        if p.exists() and p.is_dir() and not p.name.startswith("."):
            return p
        return None

    def _iter_valid_skill_dirs(self, target_dir: str | Path):
        """统一扫描器：迭代目标父目录下的所有合法技能子目录"""
        p = Path(target_dir).resolve()
        if not p.exists() or not p.is_dir():
            return
        for child in p.iterdir():
            if valid_child := self._get_valid_skill_dir(child):
                yield valid_child

    async def load_local_skill(
        self, path: str | Path, namespace: str | None = None
    ) -> Skill:
        """
        [无痕加载] 读取指定路径的技能并返回独立的 Skill 实例。
        该技能不会被注册到全局 manager 中，专门用于局部按需挂载。
        """
        if namespace is None:
            namespace = infer_plugin_namespace()

        skill = await self._parse_skill_from_path(path, namespace)
        if not skill:
            raise ValueError(
                f"无法从路径加载技能，请检查目录与 SKILL.md 是否合法: {path}"
            )
        return skill

    async def discover_skills(self) -> dict[str, Skill]:
        """遍历所有扫描目录，发现并合并所有合法技能"""
        async with self._discovery_lock:
            if self._skills:
                return self._skills

            for scan_dir, namespace in self._scan_dirs.items():
                for child in self._iter_valid_skill_dirs(scan_dir):
                    try:
                        skill = await self._parse_skill_metadata(child, namespace)
                        if skill:
                            self._skills[f"{namespace}::{skill.id}"] = skill
                    except Exception as e:
                        logger.error(f"解析技能 {child.name} 失败: {e}", e=e)

            logger.info(f"技能扫描完成，共加载 {len(self._skills)} 个技能。")
            return self._skills

    async def get_skill_details(
        self, name: str, namespace: str = "global"
    ) -> Skill | None:
        """获取技能详细信息，并在内存中缓存补全后的资源"""
        skills = await self.discover_skills()
        skill = skills.get(f"{namespace}::{name}") or skills.get(f"global::{name}")
        if not skill:
            return None
        if skill.disclosure_level < RESOURCES:
            skill = self._load_skill_resources(skill)
            async with self._discovery_lock:
                self._skills[f"{skill.namespace}::{skill.id}"] = skill
        return skill

    async def resolve_mixed_skills(
        self, skills: Sequence[Any], namespace: str | None = None
    ) -> list[Skill]:
        """统一解析混合类型的技能列表(str, Path, Skill, SkillSource)"""
        caller_namespace = namespace or infer_plugin_namespace()

        resolved = []
        resolvers = [self._normalize_to_resolver(s) for s in skills]
        for r in resolvers:
            resolved.extend(await r.resolve(self, caller_namespace))

        return resolved

    def _normalize_to_resolver(self, item: Any) -> "BaseSkillResolver":
        if isinstance(item, Skill):
            return SkillInstanceResolver(item)
        elif isinstance(item, str):
            return StringSkillResolver(item)
        elif isinstance(item, Path):
            return PathSkillResolver(item)
        elif type(item).__name__ == "SkillSource":
            return SourceSkillResolver(item)
        raise TypeError(f"无法将对象 {type(item)} 解析为技能源。")

    async def read_skill_resource(self, skill: Skill, file_path: str) -> str | None:
        """提供统一的资源文件（如 markdown 参考、脚本等）物理读取接口"""
        target_file = (skill.path / file_path).resolve()
        if not target_file.is_file():
            fallback_file = (skill.path / "scripts" / file_path).resolve()
            if fallback_file.is_file():
                target_file = fallback_file

        if target_file.is_file() and target_file.is_relative_to(skill.path.resolve()):
            return target_file.read_text(encoding="utf-8")
        return None

    async def clear_cache(self):
        """清空缓存以重新扫描(方便测试热更)"""
        async with self._discovery_lock:
            self._skills.clear()

    async def _parse_skill_from_path(
        self, path: str | Path, namespace: str
    ) -> Skill | None:
        """按径解析技能，返回提权到最高级别的孤立技能实例，不污染全局状态"""
        p = self._get_valid_skill_dir(path)
        if not p:
            logger.warning(
                f"[SkillManager] 忽略无效技能目录 (不存在、非目录或为隐藏目录): {path}"
            )
            return None
        try:
            skill = await self._parse_skill_metadata(p, namespace)
            if skill:
                skill = self._load_skill_resources(skill)
            return skill
        except Exception as e:
            logger.error(f"解析孤立技能 {p.name} 失败: {e}", e=e)
            return None

    async def _parse_frontmatter_and_body(
        self, skill_dir: Path
    ) -> tuple[dict | None, str]:
        skill_md_path = skill_dir / "SKILL.md"
        if not skill_md_path.exists():
            return None, ""
        async with aiofiles.open(skill_md_path, encoding="utf-8") as f:
            content = await f.read()
        if not content.startswith("---"):
            return None, ""
        match = re.search(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
        if not match:
            return None, ""
        yaml_content, body = match.groups()
        try:
            return yaml.safe_load(yaml_content), body.strip()
        except yaml.YAMLError as e:
            logger.warning(f"Skill {skill_dir.name} YAML 解析失败: {e}")
            return None, ""

    async def _parse_skill_metadata(
        self, skill_dir: Path, namespace: str
    ) -> Skill | None:
        fm_dict, _ = await self._parse_frontmatter_and_body(skill_dir)
        if (
            not isinstance(fm_dict, dict)
            or "name" not in fm_dict
            or "description" not in fm_dict
        ):
            return None

        blueprint, required_envs = SkillBlueprintBuilder.build(fm_dict)
        fm_dict["blueprint"] = blueprint
        fm_dict["required_envs"] = required_envs

        skill = Skill(
            frontmatter=SkillFrontmatter(**fm_dict),
            path=skill_dir,
            disclosure_level=METADATA,
            source="local",
            namespace=namespace,
        )

        if skill.frontmatter.required_envs:
            await skill_env_manager.ensure_template(
                namespace, skill.id, skill.frontmatter.required_envs
            )

        return skill

    def _load_skill_instructions(self, skill: Skill) -> Skill:
        if skill.disclosure_level >= INSTRUCTIONS:
            return skill
        _, body = skill_manager._parse_frontmatter_and_body_sync(skill.path)
        return skill.with_disclosure_level(level=INSTRUCTIONS, instructions=body)

    def _parse_frontmatter_and_body_sync(
        self, skill_dir: Path
    ) -> tuple[dict | None, str]:
        """同步版本的回退方法"""
        skill_md_path = skill_dir / "SKILL.md"
        if not skill_md_path.exists():
            return None, ""
        content = skill_md_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None, ""
        match = re.search(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
        if not match:
            return None, ""
        try:
            return yaml.safe_load(match.group(1)), match.group(2).strip()
        except yaml.YAMLError:
            return None, ""

    def _load_skill_resources(self, skill: Skill) -> Skill:
        if skill.disclosure_level >= RESOURCES:
            return skill
        if skill.disclosure_level < INSTRUCTIONS:
            skill = self._load_skill_instructions(skill)

        scripts = (
            [
                f.name
                for f in (skill.path / "scripts").iterdir()
                if f.is_file() and not f.name.startswith(".")
            ]
            if (skill.path / "scripts").is_dir()
            else []
        )
        refs = (
            [
                f.name
                for f in (skill.path / "references").iterdir()
                if f.is_file() and not f.name.startswith(".")
            ]
            if (skill.path / "references").is_dir()
            else []
        )
        return skill.with_disclosure_level(
            level=RESOURCES, scripts=scripts, references=refs
        )


skill_manager = SkillManager()


class BaseSkillResolver(ABC):
    """技能解析器基类，用于统一不同类型的技能加载逻辑"""

    @abstractmethod
    async def resolve(self, manager: SkillManager, namespace: str) -> list[Skill]: ...


class SkillInstanceResolver(BaseSkillResolver):
    """直接解析已有的 Skill 实例解析器"""

    def __init__(self, skill: Skill):
        self.skill = skill

    async def resolve(self, manager: SkillManager, namespace: str) -> list[Skill]:
        return [self.skill]


class StringSkillResolver(BaseSkillResolver):
    """根据技能 ID 解析技能的解析器"""

    def __init__(self, skill_id: str):
        self.skill_id = skill_id

    async def resolve(self, manager: SkillManager, namespace: str) -> list[Skill]:
        skill = await manager.get_skill_details(self.skill_id, namespace)
        return [skill] if skill else []


class PathSkillResolver(BaseSkillResolver):
    """根据本地路径解析技能的解析器"""

    def __init__(self, path: Path):
        self.path = path

    async def resolve(self, manager: SkillManager, namespace: str) -> list[Skill]:
        skill = await manager.load_local_skill(self.path, namespace)
        return [skill] if skill else []


class SourceSkillResolver(BaseSkillResolver):
    """根据 SkillSource 描述符动态扫描解析技能的解析器"""

    def __init__(self, source: Any):
        self.source = source

    async def resolve(self, manager: SkillManager, namespace: str) -> list[Skill]:
        candidates = []
        if self.source.fetch_all:
            candidates = list((await manager.discover_skills()).values())
        elif self.source.scan_dir:
            for child in manager._iter_valid_skill_dirs(self.source.scan_dir):
                sk = await manager._parse_skill_from_path(child, namespace)
                if sk:
                    candidates.append(sk)

        resolved = []
        for cand in candidates:
            if (
                not self.source.exclude_skills
                or cand.id not in self.source.exclude_skills
            ):
                resolved.append(cand)
        return resolved
