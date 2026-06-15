from __future__ import annotations

from collections.abc import Iterable
import contextlib
from dataclasses import dataclass, field
import html
import re
from typing import Any, Literal
import weakref

from loguru import logger
from nonebot.matcher import Matcher

ActivationDecision = Literal["match", "miss", "unknown"]
ActivationLane = Literal[
    "command_exact",
    "command_shortcut",
    "command_regex",
    "system",
    "passive_light",
    "passive_db",
    "passive_http",
    "passive_ai",
    "passive_render",
]

KNOWN_SAFE_RULE_NAMES = frozenset(
    {
        "CommandRule",
        "ShellCommandRule",
        "RegexRule",
        "StartswithRule",
        "EndswithRule",
        "FullmatchRule",
        "KeywordsRule",
        "IsTypeRule",
        "ToMeRule",
    }
)


@dataclass(frozen=True, slots=True)
class ActivationRuleDescriptor:
    kind: str
    value: object | None = None
    flags: int = 0
    ignorecase: bool = False
    deterministic_text: bool = False
    command_like: bool = False


@dataclass(slots=True)
class HandlerDescriptor:
    matcher: type[Matcher]
    module: str
    matcher_type: str
    priority: int
    lane: ActivationLane
    temp: bool = False
    block: bool = False
    command_like: bool = False
    deterministic_text: bool = False
    has_custom_rule: bool = False
    commands: tuple[str, ...] = ()
    shortcuts: tuple[str, ...] | None = None
    alconna: tuple[AlconnaDescriptor, ...] = ()
    rules: tuple[ActivationRuleDescriptor, ...] = ()


@dataclass(frozen=True, slots=True)
class AlconnaShortcutDescriptor:
    pattern: str
    fuzzy: bool = False
    prefix: bool = False
    flags: int = 0


@dataclass(frozen=True, slots=True)
class AlconnaDescriptor:
    command: str = ""
    aliases: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    shortcuts: tuple[AlconnaShortcutDescriptor, ...] = ()
    compact: bool = False
    skip_for_unmatch: bool = True
    before_rule_count: int = 0
    after_rule_count: int = 0
    before_rule_known_safe: bool = True
    after_rule_known_safe: bool = True
    input_rewrite_extensions: tuple[str, ...] = ()

    @property
    def has_reply_merge_extension(self) -> bool:
        return "ReplyMergeExtension" in self.input_rewrite_extensions

    @property
    def regex_command(self) -> bool:
        return self.command.startswith("re:")


class AlconnaActivationIndex:
    """Safe, metadata-only Alconna prefilter.

    This index never executes Alconna.parse() or Matcher.check_rule(). It only
    skips matchers when the command head/shortcut is known to miss. Anything
    custom or ambiguous stays fail-open to preserve NoneBot compatibility.
    """

    def __init__(self) -> None:
        self._safe = 0
        self._unknown = 0

    @property
    def safe_count(self) -> int:
        return self._safe

    @property
    def unknown_count(self) -> int:
        return self._unknown

    def rebuild(self, descriptors: Iterable[HandlerDescriptor]) -> None:
        safe = 0
        unknown = 0
        for descriptor in descriptors:
            if descriptor.alconna:
                if any(_alconna_can_prefilter(item) for item in descriptor.alconna):
                    safe += 1
                else:
                    unknown += 1
        self._safe = safe
        self._unknown = unknown
        if logger.level("DEBUG"):
            logger.debug(
                "alconna activation index rebuilt: safe={}, unknown={}",
                safe,
                unknown,
            )

    def select(
        self,
        descriptor: HandlerDescriptor,
        context: ActivationContext,
        texts: tuple[str, ...],
    ) -> ActivationDecision:
        if not descriptor.alconna:
            return "unknown"
        saw_unknown = False
        for alconna in descriptor.alconna:
            decision = matcher_alconna_head_matches(alconna, texts, context)
            if decision == "match":
                return "match"
            if decision == "unknown":
                saw_unknown = True
        return "unknown" if saw_unknown else "miss"


@dataclass(slots=True)
class ActivationContext:
    event_type: str
    event: object | None = None
    plain_text: str = ""
    raw_text: str = ""
    to_me: bool = False
    has_url: bool = False
    has_image: bool = False
    is_command_like: bool = False
    route_modules: set[str] = field(default_factory=set)
    ai_route_modules: set[str] = field(default_factory=set)
    ai_route_heads: set[str] = field(default_factory=set)


@dataclass(slots=True)
class ActivationResult:
    selected: list[type[Matcher]]
    deterministic_selected: set[type[Matcher]] = field(default_factory=set)
    total_descriptors: int = 0
    candidate_count: int = 0


class HandlerActivationIndex:
    """In-memory matcher activation index.

    The index is intentionally fail-open: only known NoneBot rule misses are
    rejected before matcher task creation. Custom rules, incomplete command
    metadata, and Alconna shortcut misses stay selected so plugin compatibility
    wins over dispatch aggressiveness.
    """

    def __init__(self) -> None:
        self._by_priority: dict[int, list[HandlerDescriptor]] = {}
        self._matcher_map: dict[type[Matcher], HandlerDescriptor] = {}
        self._source_keys: set[tuple[int, tuple[int, ...]]] = set()
        self._alconna_index = AlconnaActivationIndex()
        self._compiled = False

    @property
    def compiled(self) -> bool:
        return self._compiled

    def rebuild(self, matchers: dict[int, list[type[Matcher]]]) -> None:
        self._by_priority.clear()
        self._matcher_map.clear()
        source_keys: set[tuple[int, tuple[int, ...]]] = set()
        for priority, priority_matchers in matchers.items():
            descriptors = [
                self._build_descriptor(matcher, priority)
                for matcher in priority_matchers
            ]
            self._by_priority[priority] = descriptors
            for descriptor in descriptors:
                self._matcher_map[descriptor.matcher] = descriptor
            source_keys.add(
                (int(priority), tuple(id(matcher) for matcher in priority_matchers))
            )
        self._source_keys = source_keys
        self._alconna_index.rebuild(self._matcher_map.values())
        self._compiled = True

    def ensure_fresh(self, matchers: dict[int, list[type[Matcher]]]) -> None:
        source_keys = {
            (int(priority), tuple(id(matcher) for matcher in items))
            for priority, items in matchers.items()
        }
        if not self._compiled or source_keys != self._source_keys:
            self.rebuild(matchers)

    def descriptors_for_priority(
        self,
        priority: int,
        priority_matchers: Iterable[type[Matcher]],
    ) -> list[HandlerDescriptor]:
        priority_matchers_list = list(priority_matchers)
        descriptors = self._by_priority.get(priority)
        if descriptors is not None and len(descriptors) == len(priority_matchers_list):
            return descriptors
        # Fallback for dynamic matcher list changes inside a priority bucket.
        return [
            self._matcher_map.get(matcher) or self._build_descriptor(matcher, priority)
            for matcher in priority_matchers_list
        ]

    def descriptor_for(self, matcher: type[Matcher]) -> HandlerDescriptor | None:
        return self._matcher_map.get(matcher)

    def select_priority(
        self,
        priority: int,
        priority_matchers: list[type[Matcher]],
        context: ActivationContext,
        budget: dict[str, int],
    ) -> ActivationResult:
        result = ActivationResult(selected=[], total_descriptors=len(priority_matchers))
        descriptors = [
            self._matcher_map.get(matcher) or self._build_descriptor(matcher, priority)
            for matcher in priority_matchers
        ]
        for descriptor in descriptors:
            decision = self._select_descriptor(descriptor, context)
            if decision == "miss":
                continue
            if decision == "deterministic":
                result.selected.append(descriptor.matcher)
                result.deterministic_selected.add(descriptor.matcher)
                continue
            if _is_throttleable_broad_passive(descriptor, context):
                if not _consume_broad_passive_budget(descriptor, budget):
                    continue
            result.selected.append(descriptor.matcher)
        result.candidate_count = len(result.selected)
        return result

    def _select_descriptor(
        self,
        descriptor: HandlerDescriptor,
        context: ActivationContext,
    ) -> Literal["select", "miss", "deterministic"]:
        if descriptor.temp:
            return "select"
        matcher_type = descriptor.matcher_type
        if matcher_type and matcher_type != context.event_type:
            return "miss"
        if context.event_type != "message":
            return "miss" if descriptor.command_like else "select"
        if descriptor.command_like:
            return self._select_command_descriptor(descriptor, context)
        rule_match = matcher_rule_matches_text(
            descriptor.rules,
            context.raw_text,
            context.plain_text,
            event=context.event,
            to_me=context.to_me,
        )
        if rule_match == "match":
            return "deterministic"
        if rule_match == "miss":
            return "miss"
        return "select"

    def _select_command_descriptor(
        self,
        descriptor: HandlerDescriptor,
        context: ActivationContext,
    ) -> Literal["select", "miss", "deterministic"]:
        texts = text_match_candidates(
            context.plain_text,
            context.raw_text,
            context.event,
        )
        if not texts:
            return "select"
        rule_match = matcher_rule_matches_text(
            descriptor.rules,
            context.raw_text,
            context.plain_text,
            event=context.event,
            to_me=context.to_me,
        )
        if rule_match == "miss":
            return "miss"
        command_matched = False
        if descriptor.alconna:
            alconna_match = self._alconna_index.select(descriptor, context, texts)
            if alconna_match == "match":
                command_matched = True
            elif alconna_match == "miss":
                return "miss"
            else:
                return "select"
        elif descriptor.commands:
            if any(
                matcher_command_matches(text, command)
                for text in texts
                for command in descriptor.commands
            ):
                command_matched = True
            else:
                shortcut_match = matcher_alconna_shortcut_matches_any(
                    descriptor.shortcuts,
                    texts,
                )
                if shortcut_match == "match":
                    command_matched = True
                else:
                    return "miss" if not descriptor.has_custom_rule else "select"
        else:
            shortcut_match = matcher_alconna_shortcut_matches_any(
                descriptor.shortcuts,
                texts,
            )
            if shortcut_match == "match":
                command_matched = True

        if (
            rule_match == "match"
            and not descriptor.has_custom_rule
            and descriptor.shortcuts is None
            and not descriptor.alconna
        ):
            command_matched = True
        if (
            not (descriptor.commands or descriptor.shortcuts is not None)
            and not command_matched
            and not descriptor.alconna
        ):
            return "select"

        if (
            context.ai_route_modules
            and descriptor.module not in context.ai_route_modules
        ):
            if not matcher_matches_ai_route_heads(descriptor, context.ai_route_heads):
                return "select"

        if command_matched:
            return "select" if descriptor.has_custom_rule else "deterministic"
        return "select"

    def _build_descriptor(
        self,
        matcher: type[Matcher],
        priority: int,
    ) -> HandlerDescriptor:
        rules = extract_matcher_rule_descriptors(matcher)
        command_like = any(rule.command_like for rule in rules)
        deterministic = any(rule.deterministic_text for rule in rules)
        if hasattr(matcher, "command"):
            command_like = True
        commands = extract_matcher_command_literals(matcher) or ()
        alconna_descriptors = extract_matcher_alconna_descriptors(matcher)
        shortcuts = extract_matcher_alconna_shortcuts(matcher)
        if alconna_descriptors:
            command_like = True
        if shortcuts is not None:
            command_like = True
        module = matcher_module_name(matcher)
        lane = classify_lane(
            matcher,
            module=module,
            command_like=command_like,
            deterministic_text=deterministic,
            shortcuts=shortcuts,
            commands=commands,
        )
        return HandlerDescriptor(
            matcher=matcher,
            module=module,
            matcher_type=getattr(matcher, "type", "") or "",
            priority=priority,
            lane=lane,
            temp=bool(getattr(matcher, "temp", False)),
            block=bool(getattr(matcher, "block", False)),
            command_like=command_like,
            deterministic_text=deterministic,
            has_custom_rule=matcher_has_custom_rule(matcher),
            commands=commands,
            shortcuts=shortcuts,
            alconna=alconna_descriptors,
            rules=rules,
        )


def matcher_module_name(matcher_cls: type[Matcher]) -> str:
    module = getattr(matcher_cls, "plugin_name", "") or ""
    if module:
        return module
    plugin = getattr(matcher_cls, "plugin", None)
    if not plugin:
        return ""
    return (getattr(plugin, "name", "") or "").strip()


def classify_lane(
    matcher_cls: type[Matcher],
    *,
    module: str,
    command_like: bool,
    deterministic_text: bool,
    shortcuts: tuple[str, ...] | None,
    commands: tuple[str, ...],
) -> ActivationLane:
    if getattr(matcher_cls, "temp", False):
        return "system"
    if command_like:
        if shortcuts:
            return "command_shortcut"
        has_regex_command = any(
            is_regex_like_command_literal(item) for item in commands
        )
        if deterministic_text or has_regex_command:
            return "command_regex"
        return "command_exact"
    module_l = (module or "").casefold()
    if any(hint in module_l for hint in PASSIVE_AI_HINTS):
        return "passive_ai"
    if any(hint in module_l for hint in PASSIVE_RENDER_HINTS):
        return "passive_render"
    if any(hint in module_l for hint in PASSIVE_HTTP_HINTS):
        return "passive_http"
    if matcher_has_custom_rule(matcher_cls):
        return "passive_light"
    if any(hint in module_l for hint in PASSIVE_DB_HINTS):
        return "passive_db"
    return "passive_light"


def matcher_has_custom_rule(matcher_cls: type[Matcher]) -> bool:
    rule = getattr(matcher_cls, "rule", None)
    checkers = getattr(rule, "checkers", ()) or ()
    for checker in checkers:
        call = getattr(checker, "call", None)
        if call is None:
            continue
        call_module = call.__class__.__module__
        if call_module.startswith("nonebot.rule") or call_module.startswith(
            "nonebot_plugin_alconna.rule"
        ):
            continue
        return True
    return False


def matcher_is_command_like(matcher_cls: type[Matcher]) -> bool:
    rules = extract_matcher_rule_descriptors(matcher_cls)
    if any(rule.command_like for rule in rules):
        return True
    if hasattr(matcher_cls, "command"):
        return True
    if extract_matcher_alconna_descriptors(matcher_cls):
        return True
    return extract_matcher_alconna_shortcuts(matcher_cls) is not None


def matcher_has_deterministic_text_rule(matcher_cls: type[Matcher]) -> bool:
    return any(
        rule.deterministic_text
        for rule in extract_matcher_rule_descriptors(matcher_cls)
    )


def classify_matcher_lane(
    matcher_cls: type[Matcher],
    *,
    ai_route_modules: set[str] | None = None,
) -> ActivationLane:
    module = matcher_module_name(matcher_cls)
    if ai_route_modules and any(
        module.casefold() == route_module.casefold()
        for route_module in ai_route_modules
    ):
        return "passive_ai"
    rules = extract_matcher_rule_descriptors(matcher_cls)
    command_like = any(rule.command_like for rule in rules)
    deterministic = any(rule.deterministic_text for rule in rules)
    if hasattr(matcher_cls, "command"):
        command_like = True
    commands = extract_matcher_command_literals(matcher_cls) or ()
    alconna_descriptors = extract_matcher_alconna_descriptors(matcher_cls)
    shortcuts = extract_matcher_alconna_shortcuts(matcher_cls)
    if alconna_descriptors:
        command_like = True
    if shortcuts is not None:
        command_like = True
    return classify_lane(
        matcher_cls,
        module=module,
        command_like=command_like,
        deterministic_text=deterministic,
        shortcuts=shortcuts,
        commands=commands,
    )


def extract_matcher_rule_descriptors(
    matcher_cls: type[Matcher],
) -> tuple[ActivationRuleDescriptor, ...]:
    descriptors: list[ActivationRuleDescriptor] = []
    rule = getattr(matcher_cls, "rule", None)
    checkers = getattr(rule, "checkers", ()) or ()
    for checker in checkers:
        call = getattr(checker, "call", None)
        if call is None:
            continue
        call_module = call.__class__.__module__
        call_name = call.__class__.__name__
        if call_module.startswith("nonebot.rule"):
            if call_name == "CommandRule":
                descriptors.append(
                    ActivationRuleDescriptor(
                        "command",
                        getattr(call, "cmds", ()),
                        command_like=True,
                    )
                )
            elif call_name == "ShellCommandRule":
                descriptors.append(
                    ActivationRuleDescriptor(
                        "shell_command",
                        getattr(call, "cmds", ()),
                        command_like=True,
                    )
                )
            elif call_name == "RegexRule":
                descriptors.append(
                    ActivationRuleDescriptor(
                        "regex",
                        getattr(call, "regex", ""),
                        flags=int(getattr(call, "flags", 0) or 0),
                        deterministic_text=True,
                        command_like=True,
                    )
                )
            elif call_name == "StartswithRule":
                descriptors.append(
                    ActivationRuleDescriptor(
                        "startswith",
                        normalize_rule_string_tuple(getattr(call, "msg", ())),
                        ignorecase=bool(getattr(call, "ignorecase", False)),
                        deterministic_text=True,
                        command_like=True,
                    )
                )
            elif call_name == "EndswithRule":
                descriptors.append(
                    ActivationRuleDescriptor(
                        "endswith",
                        normalize_rule_string_tuple(getattr(call, "msg", ())),
                        ignorecase=bool(getattr(call, "ignorecase", False)),
                        deterministic_text=True,
                        command_like=True,
                    )
                )
            elif call_name == "FullmatchRule":
                descriptors.append(
                    ActivationRuleDescriptor(
                        "fullmatch",
                        normalize_rule_string_tuple(getattr(call, "msg", ())),
                        ignorecase=bool(getattr(call, "ignorecase", False)),
                        deterministic_text=True,
                        command_like=True,
                    )
                )
            elif call_name == "KeywordsRule":
                descriptors.append(
                    ActivationRuleDescriptor(
                        "keywords",
                        normalize_rule_string_tuple(getattr(call, "keywords", ())),
                        deterministic_text=True,
                        command_like=True,
                    )
                )
            elif call_name == "IsTypeRule":
                descriptors.append(
                    ActivationRuleDescriptor("is_type", getattr(call, "types", ()))
                )
            elif call_name == "ToMeRule":
                descriptors.append(ActivationRuleDescriptor("to_me"))
            else:
                descriptors.append(ActivationRuleDescriptor("custom"))
        elif (
            call_module.startswith("nonebot_plugin_alconna.rule")
            and call_name == "AlconnaRule"
        ):
            descriptors.append(ActivationRuleDescriptor("alconna", command_like=True))
        else:
            descriptors.append(ActivationRuleDescriptor("custom"))
    return tuple(descriptors)


def normalize_rule_string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple | set | frozenset):
        return tuple(str(item) for item in value if str(item))
    return ()


def text_match_candidates(
    plain_text: str,
    raw_text: str = "",
    event: object | None = None,
) -> tuple[str, ...]:
    """Return text variants visible to different matcher rule providers."""

    candidates: list[str] = []

    def add(text: object) -> None:
        if not isinstance(text, str):
            return
        normalized = text.strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)

        unescaped = _unescape_message_text(normalized)
        if unescaped and unescaped not in candidates:
            candidates.append(unescaped)

    add(plain_text)
    if event is not None:
        with contextlib.suppress(Exception):
            getter = getattr(event, "get_plaintext", None)
            if callable(getter):
                add(getter())
    add(raw_text)
    return tuple(candidates)


def _unescape_message_text(text: str) -> str:
    if not text:
        return ""
    unescaped = html.unescape(text)
    return unescaped.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")


def matcher_rule_matches_text(
    descriptors: tuple[ActivationRuleDescriptor, ...],
    raw_text: str,
    plain_text: str,
    *,
    event: object | None = None,
    to_me: bool = False,
) -> ActivationDecision:
    matched_any = False
    saw_deterministic = False
    saw_unknown = False
    message_text = raw_text or plain_text
    plain_candidates = text_match_candidates(plain_text, raw_text, event)

    for descriptor in descriptors:
        kind = descriptor.kind
        if kind in {"custom", "alconna"}:
            saw_unknown = True
            continue
        if kind in {"command", "shell_command"}:
            saw_deterministic = True
            commands: set[str] = set()
            collect_command_literals(descriptor.value, commands)
            normalized_commands = {
                normalized
                for item in commands
                if (normalized := normalize_command(item))
            }
            if any(
                matcher_command_matches(text, command)
                for text in plain_candidates
                for command in normalized_commands
            ):
                matched_any = True
            else:
                return "miss"
        elif kind in {"regex", "regex_fullmatch"}:
            saw_deterministic = True
            pattern = str(descriptor.value or "")
            if not pattern:
                continue
            try:
                matched = (
                    re.fullmatch(pattern, message_text, descriptor.flags)
                    if kind == "regex_fullmatch"
                    else re.search(pattern, message_text, descriptor.flags)
                )
                if matched:
                    matched_any = True
                else:
                    return "miss"
            except re.error:
                return "unknown"
        elif kind == "startswith":
            saw_deterministic = True
            values = descriptor.value if isinstance(descriptor.value, tuple) else ()
            candidates = (
                tuple(item.casefold() for item in values)
                if descriptor.ignorecase
                else values
            )
            texts = (
                tuple(item.casefold() for item in plain_candidates)
                if descriptor.ignorecase
                else plain_candidates
            )
            if any(
                text.startswith(item) for text in texts for item in candidates if item
            ):
                matched_any = True
            else:
                return "miss"
        elif kind == "endswith":
            saw_deterministic = True
            values = descriptor.value if isinstance(descriptor.value, tuple) else ()
            candidates = (
                tuple(item.casefold() for item in values)
                if descriptor.ignorecase
                else values
            )
            texts = (
                tuple(item.casefold() for item in plain_candidates)
                if descriptor.ignorecase
                else plain_candidates
            )
            if any(
                text.endswith(item) for text in texts for item in candidates if item
            ):
                matched_any = True
            else:
                return "miss"
        elif kind == "fullmatch":
            saw_deterministic = True
            values = descriptor.value if isinstance(descriptor.value, tuple) else ()
            candidates = (
                tuple(item.casefold() for item in values)
                if descriptor.ignorecase
                else values
            )
            texts = (
                tuple(item.casefold() for item in plain_candidates)
                if descriptor.ignorecase
                else plain_candidates
            )
            if any(text in candidates for text in texts):
                matched_any = True
            else:
                return "miss"
        elif kind == "keywords":
            saw_deterministic = True
            values = descriptor.value if isinstance(descriptor.value, tuple) else ()
            if any(
                item and item in text for text in plain_candidates for item in values
            ):
                matched_any = True
            else:
                return "miss"
        elif kind == "to_me":
            if not to_me:
                return "miss"
        elif kind == "is_type":
            if event is None:
                return "unknown"
            types = descriptor.value
            if isinstance(types, type):
                if not isinstance(event, types):
                    return "miss"
            elif isinstance(types, tuple) and types:
                if not isinstance(event, types):
                    return "miss"
    if matched_any:
        return "unknown" if saw_unknown else "match"
    if saw_unknown:
        return "unknown"
    if saw_deterministic:
        return "miss"
    return "unknown"


def extract_matcher_command_literals(
    matcher_cls: type[Matcher],
) -> tuple[str, ...] | None:
    commands: set[str] = set()
    collect_command_literals(getattr(matcher_cls, "command", None), commands)
    rule = getattr(matcher_cls, "rule", None)
    checkers = getattr(rule, "checkers", ()) or ()
    for checker in checkers:
        call = getattr(checker, "call", None)
        if call is None:
            continue
        for attr in ("cmds", "command", "commands", "cmd"):
            collect_command_literals(getattr(call, attr, None), commands)
    normalized_commands = {
        normalized for item in commands if (normalized := normalize_command(item))
    }
    normalized = tuple(sorted(normalized_commands))
    return normalized or None


def collect_command_literals(value: Any, target: set[str], depth: int = 0) -> None:
    if depth > 3 or value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            target.add(text)
        return
    if isinstance(value, weakref.ReferenceType):
        resolved = value()
        if resolved is not None and resolved is not value:
            collect_command_literals(resolved, target, depth + 1)
        return
    if isinstance(value, list | tuple | set | frozenset):
        if all(isinstance(item, str) for item in value):
            parts = tuple(str(item).strip() for item in value if str(item).strip())
            if parts:
                target.add(" ".join(parts))
                target.add("".join(parts))
            return
        for item in value:
            collect_command_literals(item, target, depth + 1)
        return
    if callable(value) and getattr(value, "__self__", None) is not None:
        with contextlib.suppress(TypeError, RuntimeError, ReferenceError):
            resolved = value()
            if resolved is not None and resolved is not value:
                collect_command_literals(resolved, target, depth + 1)
                return
    for attr in (
        "name",
        "path",
        "aliases",
        "header_display",
        "command",
        "commands",
        "cmd",
        "cmds",
    ):
        nested = getattr(value, attr, None)
        if nested is not None and nested is not value:
            collect_command_literals(nested, target, depth + 1)


def extract_matcher_alconna_shortcuts(
    matcher_cls: type[Matcher],
) -> tuple[str, ...] | None:
    shortcuts: set[str] = set()
    for attr in ("command", "_rule", "rule"):
        collect_alconna_shortcuts(getattr(matcher_cls, attr, None), shortcuts)
    rule = getattr(matcher_cls, "rule", None)
    checkers = getattr(rule, "checkers", ()) or ()
    for checker in checkers:
        call = getattr(checker, "call", None)
        if call is None:
            continue
        if call.__class__.__name__ != "AlconnaRule":
            continue
        command = resolve_maybe_weakref(
            getattr(call, "command", None) or getattr(call, "alconna", None)
        )
        collect_alconna_shortcuts(command, shortcuts)
    normalized_shortcuts = {
        normalized
        for item in shortcuts
        if item and (normalized := normalize_shortcut_pattern(item))
    }
    normalized = tuple(sorted(normalized_shortcuts))
    return normalized if normalized else None


def collect_alconna_shortcuts(value: Any, target: set[str], depth: int = 0) -> None:
    if depth > 4 or value is None:
        return
    if isinstance(value, weakref.ReferenceType):
        resolved = value()
        if resolved is not None and resolved is not value:
            collect_alconna_shortcuts(resolved, target, depth + 1)
        return
    get_shortcuts = getattr(value, "get_shortcuts", None)
    if callable(get_shortcuts):
        with contextlib.suppress(Exception):
            raw_shortcuts = get_shortcuts()
            if isinstance(raw_shortcuts, list | tuple | set | frozenset):
                for shortcut in raw_shortcuts:
                    if isinstance(shortcut, str) and shortcut.strip():
                        target.add(shortcut.strip())
    elif callable(value):
        with contextlib.suppress(Exception):
            resolved = value()
            if resolved is not None and resolved is not value:
                collect_alconna_shortcuts(resolved, target, depth + 1)
                return
    formatter = getattr(value, "formatter", None)
    data = getattr(formatter, "data", None)
    if isinstance(data, dict):
        for trace in data.values():
            trace_shortcuts = getattr(trace, "shortcuts", None)
            if not isinstance(trace_shortcuts, dict):
                continue
            for shortcut in trace_shortcuts:
                if isinstance(shortcut, str) and shortcut.strip():
                    target.add(shortcut.strip())
    for attr in ("shortcut", "shortcuts"):
        shortcuts = getattr(value, attr, None)
        if isinstance(shortcuts, dict):
            for key in shortcuts:
                if isinstance(key, str) and key.strip():
                    target.add(key.strip())
        elif isinstance(shortcuts, list | tuple | set | frozenset):
            for item in shortcuts:
                if isinstance(item, str) and item.strip():
                    target.add(item.strip())
    with contextlib.suppress(Exception):
        from arclet.alconna import command_manager

        for shortcut_map in command_manager.get_shortcut(value).values():  # type: ignore[arg-type]
            origin_key = getattr(shortcut_map, "origin_key", None)
            if isinstance(origin_key, str) and origin_key.strip():
                target.add(origin_key.strip())
    for attr in ("command", "commands", "base", "formatter", "source"):
        nested = getattr(value, attr, None)
        if nested is not None and nested is not value:
            collect_alconna_shortcuts(nested, target, depth + 1)


def extract_matcher_alconna_descriptors(
    matcher_cls: type[Matcher],
) -> tuple[AlconnaDescriptor, ...]:
    descriptors: list[AlconnaDescriptor] = []
    rule = getattr(matcher_cls, "rule", None)
    checkers = getattr(rule, "checkers", ()) or ()
    for checker in checkers:
        call = getattr(checker, "call", None)
        if call is None:
            continue
        if call.__class__.__name__ != "AlconnaRule":
            continue
        if not call.__class__.__module__.startswith("nonebot_plugin_alconna.rule"):
            continue
        command = resolve_maybe_weakref(
            getattr(call, "command", None) or getattr(call, "alconna", None)
        )
        descriptor = _build_alconna_descriptor(call, command)
        if descriptor is not None:
            descriptors.append(descriptor)
    return tuple(descriptors)


def _build_alconna_descriptor(call: Any, command: Any) -> AlconnaDescriptor | None:
    if command is None:
        return None
    command_text = str(getattr(command, "command", "") or "").strip()
    aliases = tuple(
        str(item).strip()
        for item in getattr(command, "aliases", ()) or ()
        if str(item).strip() and str(item).strip() != command_text
    )
    prefixes = tuple(
        str(item)
        for item in getattr(command, "prefixes", ()) or ()
        if isinstance(item, str)
    )
    meta = getattr(command, "meta", None)
    shortcuts = _extract_alconna_shortcut_descriptors(command)
    return AlconnaDescriptor(
        command=command_text,
        aliases=aliases,
        prefixes=prefixes,
        shortcuts=shortcuts,
        compact=bool(getattr(meta, "compact", False)),
        skip_for_unmatch=bool(getattr(call, "skip", True)),
        before_rule_count=_rule_checker_count(getattr(call, "before_rules", None)),
        after_rule_count=_rule_checker_count(getattr(call, "after_rules", None)),
        before_rule_known_safe=_alconna_rule_is_known_safe(
            getattr(call, "before_rules", None)
        ),
        after_rule_known_safe=_alconna_rule_is_known_safe(
            getattr(call, "after_rules", None)
        ),
        input_rewrite_extensions=_extract_alconna_input_rewrite_extensions(call),
    )


def _rule_checker_count(rule: Any) -> int:
    checkers = getattr(rule, "checkers", ()) or ()
    with contextlib.suppress(TypeError):
        return len(checkers)
    return 1


def _alconna_rule_is_known_safe(rule: Any) -> bool:
    """Whether Alconna before/after Rule can be reasoned about statically.

    We still do not execute these rules here. A rule is considered safe only
    when every checker is an official NoneBot rule whose negative result can be
    reproduced by the selector. Custom rules stay fail-open.
    """

    checkers = getattr(rule, "checkers", ()) or ()
    for checker in checkers:
        call = getattr(checker, "call", None)
        if call is None:
            return False
        call_module = call.__class__.__module__
        call_name = call.__class__.__name__
        if not call_module.startswith("nonebot.rule"):
            return False
        if call_name not in KNOWN_SAFE_RULE_NAMES:
            return False
    return True


def _extract_alconna_shortcut_descriptors(
    command: Any,
) -> tuple[AlconnaShortcutDescriptor, ...]:
    shortcuts: list[AlconnaShortcutDescriptor] = []
    with contextlib.suppress(Exception):
        from arclet.alconna import command_manager

        raw_shortcuts = command_manager.get_shortcut(command)  # type: ignore[arg-type]
        if isinstance(raw_shortcuts, dict):
            for key, args in raw_shortcuts.items():
                pattern = str(key or "").strip()
                if not pattern:
                    continue
                shortcuts.append(
                    AlconnaShortcutDescriptor(
                        pattern=pattern,
                        fuzzy=bool(getattr(args, "fuzzy", False)),
                        prefix=bool(getattr(args, "prefix", False)),
                        flags=int(getattr(args, "flags", 0) or 0),
                    )
                )
    if shortcuts:
        return tuple(shortcuts)
    fallback: set[str] = set()
    collect_alconna_shortcuts(command, fallback)
    return tuple(
        AlconnaShortcutDescriptor(pattern=item)
        for item in sorted(fallback)
        if item.strip()
    )


def _extract_alconna_input_rewrite_extensions(call: Any) -> tuple[str, ...]:
    executor = getattr(call, "executor", None)
    if executor is None:
        return ()
    result: list[str] = []
    for attr in ("extensions", "_extensions", "exts", "context"):
        extensions = getattr(executor, attr, None)
        if not isinstance(extensions, list | tuple | set | frozenset):
            continue
        for extension in extensions:
            overrides = getattr(extension.__class__, "_overrides", None)
            if not isinstance(overrides, dict):
                overrides = getattr(extension, "_overrides", None)
            if not isinstance(overrides, dict):
                continue
            if not (
                bool(overrides.get("message_provider"))
                or bool(overrides.get("receive_wrapper"))
            ):
                continue
            name = extension.__class__.__name__
            if name not in result:
                result.append(name)
    return tuple(result)


def _alconna_can_prefilter(alconna: AlconnaDescriptor) -> bool:
    if not (alconna.command or alconna.aliases or alconna.shortcuts):
        return False
    if not (alconna.before_rule_known_safe and alconna.after_rule_known_safe):
        return False
    if any(name != "ReplyMergeExtension" for name in alconna.input_rewrite_extensions):
        return False
    return True


def matcher_alconna_head_matches(
    alconna: AlconnaDescriptor,
    texts: Iterable[str],
    context: ActivationContext,
) -> ActivationDecision:
    if not _alconna_can_prefilter(alconna):
        return "unknown"
    if alconna.has_reply_merge_extension and _event_has_reply(context.event):
        return "unknown"

    candidates = tuple(text.strip() for text in texts if text and text.strip())
    if not candidates:
        return "unknown"

    saw_unknown = False
    command_heads = (alconna.command, *alconna.aliases)
    for text in candidates:
        for command in command_heads:
            if not command:
                continue
            decision = _alconna_command_head_matches(
                text,
                command,
                alconna.prefixes,
                compact=alconna.compact,
            )
            if decision == "match":
                return "match"
            if decision == "unknown":
                saw_unknown = True
        for shortcut in alconna.shortcuts:
            decision = _alconna_shortcut_matches(text, shortcut, alconna.prefixes)
            if decision == "match":
                return "match"
            if decision == "unknown":
                saw_unknown = True
    return "unknown" if saw_unknown else "miss"


def _alconna_command_head_matches(
    text: str,
    command: str,
    prefixes: tuple[str, ...],
    *,
    compact: bool,
) -> ActivationDecision:
    normalized = command.strip()
    if not normalized:
        return "unknown"
    if normalized.startswith("re:"):
        pattern = normalized.removeprefix("re:").strip()
        if not pattern:
            return "unknown"
        for prefix in prefixes or ("",):
            try:
                if re.match(rf"^{re.escape(prefix)}(?:{pattern})", text):
                    return "match"
            except re.error:
                return "unknown"
        return "miss"
    for prefix in prefixes or ("",):
        head = f"{prefix}{normalized}"
        if _alconna_literal_head_matches(text, head, compact=compact):
            return "match"
    return "miss"


def _alconna_literal_head_matches(text: str, head: str, *, compact: bool) -> bool:
    if not text or not head:
        return False
    if text == head:
        return True
    if not text.startswith(head):
        return False
    if len(text) == len(head):
        return True
    rest = text[len(head) :]
    if rest and rest[0].isspace():
        return True
    return bool(compact or not head[-1].isascii())


def _alconna_shortcut_matches(
    text: str,
    shortcut: AlconnaShortcutDescriptor,
    prefixes: tuple[str, ...] = (),
) -> ActivationDecision:
    pattern = shortcut.pattern.strip()
    if not pattern:
        return "unknown"
    saw_unknown = False
    for normalized in _alconna_shortcut_patterns(pattern, shortcut, prefixes):
        decision = _alconna_shortcut_pattern_matches(text, normalized, shortcut)
        if decision == "match":
            return "match"
        if decision == "unknown":
            saw_unknown = True
    return "unknown" if saw_unknown else "miss"


def _alconna_shortcut_patterns(
    pattern: str,
    shortcut: AlconnaShortcutDescriptor,
    prefixes: tuple[str, ...],
) -> tuple[str, ...]:
    normalized = normalize_shortcut_pattern(pattern)
    if not normalized:
        return ()
    patterns = [normalized]
    if shortcut.prefix:
        for prefix in prefixes or ("",):
            candidate = normalize_shortcut_pattern(f"{prefix}{normalized}")
            if candidate and candidate not in patterns:
                patterns.append(candidate)
    return tuple(patterns)


def _alconna_shortcut_pattern_matches(
    text: str,
    normalized: str,
    shortcut: AlconnaShortcutDescriptor,
) -> ActivationDecision:
    placeholder_match = _placeholder_shortcut_decision(text, normalized)
    if placeholder_match == "match":
        return "match"
    if placeholder_match == "unknown":
        return "unknown"
    if not is_regex_like_shortcut(normalized) and matcher_command_matches(
        text,
        normalized,
    ):
        return "match"
    try:
        if shortcut.fuzzy:
            return (
                "match" if re.match(f"^{normalized}", text, shortcut.flags) else "miss"
            )
        return "match" if re.fullmatch(normalized, text, shortcut.flags) else "miss"
    except re.error:
        return "unknown"


def _event_has_reply(event: object | None) -> bool:
    if event is None:
        return False
    with contextlib.suppress(Exception):
        message = event.get_message()  # type: ignore[attr-defined]
        for segment in message:
            segment_type = getattr(segment, "type", None)
            if segment_type == "reply":
                return True
            if isinstance(segment, dict) and segment.get("type") == "reply":
                return True
    for attr in ("reply", "reply_message", "source"):
        with contextlib.suppress(Exception):
            if getattr(event, attr, None) is not None:
                return True
    raw_text = ""
    with contextlib.suppress(Exception):
        raw_text = str(event.get_message())  # type: ignore[attr-defined]
    lowered = raw_text.casefold()
    return any(
        marker in lowered
        for marker in ("[cq:reply", "type=reply", '"reply"', "'reply'")
    )


def resolve_maybe_weakref(value: Any) -> Any:
    if isinstance(value, weakref.ReferenceType):
        resolved = value()
        return resolved if resolved is not None else value
    if callable(value) and getattr(value, "__self__", None) is not None:
        with contextlib.suppress(TypeError, RuntimeError, ReferenceError):
            resolved = value()
            if resolved is not None and resolved is not value:
                return resolved
    return value


def normalize_command(command: str) -> str:
    text = command.strip()
    if not text:
        return ""
    text = re.sub(r"^(?:\s*(?:\[[^\]]*]|\<[^>]*>))+\s*", "", text)
    cut_points = [idx for idx in (text.find("["), text.find("<")) if idx >= 0]
    if cut_points:
        text = text[: min(cut_points)]
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?:\s+[?*]+|[?*]+)$", "", text).strip()
    return text


def matcher_command_matches(text: str, command: str) -> bool:
    normalized = command.strip()
    if not normalized:
        return False
    if normalized.startswith("re:"):
        pattern = normalized.removeprefix("re:").strip()
        if not pattern:
            return False
        try:
            return re.search(pattern, text) is not None
        except re.error:
            return False
    if command_matches(text, normalized):
        return True
    return text.startswith(normalized) and not normalized[-1].isascii()


def command_matches(text: str, command: str) -> bool:
    if not text or not command:
        return False
    if text == command:
        return True
    if text.startswith(command):
        if len(text) == len(command):
            return True
        return text[len(command)].isspace()
    return False


def is_regex_like_command_literal(command: str) -> bool:
    text = command.strip()
    if not text:
        return False
    if text.startswith("re:"):
        return True
    return any(token in text for token in ("\\", "(", ")", "[", "]", "|", "^", "$"))


def normalize_shortcut_pattern(pattern: str) -> str:
    text = str(pattern or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\[(?:[^\]]*)\]\s*", "", text)
    text = re.sub(r"\s*\.\.\.args?$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*\.\.\.$", "", text).strip()
    text = re.sub(r"^\^", "", text)
    return text


def matcher_alconna_shortcut_matches(
    shortcuts: tuple[str, ...] | None,
    text: str,
) -> ActivationDecision:
    if shortcuts is None:
        return "unknown"
    for shortcut in shortcuts:
        if shortcut_matches_text(text, shortcut):
            return "match"
    return "unknown"


def matcher_alconna_shortcut_matches_any(
    shortcuts: tuple[str, ...] | None,
    texts: Iterable[str],
) -> ActivationDecision:
    if shortcuts is None:
        return "unknown"
    for text in texts:
        if matcher_alconna_shortcut_matches(shortcuts, text) == "match":
            return "match"
    return "unknown"


def shortcut_matches_text(text: str, shortcut: str) -> bool:
    pattern = normalize_shortcut_pattern(shortcut)
    if not pattern:
        return False
    if placeholder_shortcut_matches(text, pattern):
        return True
    if is_regex_like_shortcut(pattern):
        try:
            return re.match(pattern, text) is not None
        except re.error:
            return False
    return matcher_command_matches(text, pattern)


def placeholder_shortcut_matches(text: str, pattern: str) -> bool:
    return _placeholder_shortcut_decision(text, pattern) == "match"


def _placeholder_shortcut_decision(text: str, pattern: str) -> ActivationDecision:
    if "{" not in pattern or "}" not in pattern:
        return "miss"
    pieces: list[str] = []
    last = 0
    for match in re.finditer(r"\{[^{}]+\}", pattern):
        pieces.append(re.escape(pattern[last : match.start()]))
        pieces.append(r"\S+")
        last = match.end()
    if not pieces:
        return "miss"
    pieces.append(re.escape(pattern[last:]))
    try:
        return (
            "match"
            if re.match(rf"^{''.join(pieces)}(?:\s|$)", text) is not None
            else "miss"
        )
    except re.error:
        return "unknown"


def is_regex_like_shortcut(pattern: str) -> bool:
    return any(token in pattern for token in ("\\", "(", ")", "[", "]", "|", "^", "$"))


def matcher_matches_ai_route_heads(
    descriptor: HandlerDescriptor,
    ai_route_heads: set[str],
) -> bool:
    if not ai_route_heads:
        return False
    for command in descriptor.commands:
        normalized_command = command.strip().casefold()
        if not normalized_command:
            continue
        for head in ai_route_heads:
            if not head:
                continue
            if matcher_command_matches(head, normalized_command) or command_matches(
                normalized_command,
                head,
            ):
                return True
    for shortcut in descriptor.shortcuts or ():
        for head in ai_route_heads:
            if head and shortcut_matches_text(head, shortcut):
                return True
    return False


def _is_throttleable_broad_passive(
    descriptor: HandlerDescriptor,
    context: ActivationContext,
) -> bool:
    """Only broad, no-rule passive message matchers may be budget-throttled."""

    if context.event_type != "message":
        return False
    if descriptor.temp or descriptor.lane == "system":
        return False
    if not descriptor.lane.startswith("passive_"):
        return False
    if descriptor.command_like or descriptor.deterministic_text:
        return False
    if descriptor.has_custom_rule or descriptor.rules:
        return False
    if descriptor.lane == "passive_http" and (
        context.has_url or _looks_like_rich_message(context.raw_text)
    ):
        return False
    return True


def _looks_like_rich_message(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(
        marker in lowered
        for marker in (
            "[cq:json",
            "[json:",
            "[cq:xml",
            "[xml:",
            "qqdocurl",
            "jumpurl",
            "miniapp",
            "com.tencent",
        )
    )


def _consume_broad_passive_budget(
    descriptor: HandlerDescriptor,
    budget: dict[str, int],
) -> bool:
    lane = descriptor.lane
    if lane not in budget:
        return True
    if budget[lane] <= 0:
        return False
    budget[lane] -= 1
    return True


PASSIVE_DB_HINTS = (
    "word_bank",
    "black_word",
    "history",
    "statistics",
    "sign",
    "gold",
    "redbag",
    "mute",
    "group",
    "user",
    "admin",
    "ban",
    "limit",
    "check",
)
PASSIVE_HTTP_HINTS = (
    "http",
    "translate",
    "bilibili",
    "music",
    "comment",
    "nbnhhsh",
    "quote",
    "search",
    "jitang",
    "poetry",
    "anime",
    "cover",
)
PASSIVE_AI_HINTS = (
    "chatinter",
    "dialogue",
    "ai",
    "llm",
    "fudu",
    "bym_ai",
)
PASSIVE_RENDER_HINTS = (
    "render",
    "image",
    "meme",
    "memes",
    "word_cloud",
    "wordcloud",
    "pic",
    "picture",
    "coser",
    "luxun",
)


__all__ = [
    "ActivationContext",
    "ActivationDecision",
    "ActivationResult",
    "ActivationRuleDescriptor",
    "HandlerActivationIndex",
    "HandlerDescriptor",
    "classify_matcher_lane",
    "command_matches",
    "extract_matcher_alconna_shortcuts",
    "extract_matcher_command_literals",
    "extract_matcher_rule_descriptors",
    "matcher_command_matches",
    "matcher_has_custom_rule",
    "matcher_has_deterministic_text_rule",
    "matcher_is_command_like",
    "matcher_rule_matches_text",
]
