"""
Pydantic V1 & V2 兼容层模块

为 Pydantic V1 与 V2 版本提供统一的便捷函数与类，
包括 model_dump, model_copy, model_json_schema, parse_as 等。
"""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin

from nonebot.compat import (
    PYDANTIC_V2,
    model_dump,
    model_fields,
    type_validate_json,
    type_validate_python,
)
from pydantic import BaseModel
import ujson as json

T = TypeVar("T", bound=BaseModel)
V = TypeVar("V")


import typing

if typing.TYPE_CHECKING:
    _T_TA = TypeVar("_T_TA")

    class TypeAdapter(typing.Generic[_T_TA]):
        def __init__(self, type_: Any, **kwargs: Any): ...
        def validate_python(self, obj: Any) -> _T_TA: ...

    def model_validator(*args: Any, **kwargs: Any) -> Any: ...
else:
    try:
        from pydantic import TypeAdapter, model_validator
    except ImportError:

        class TypeAdapter:
            def __init__(self, type_: Any, **kwargs: Any):
                self.type_ = type_

            def validate_python(self, obj: Any) -> Any:
                from nonebot.compat import type_validate_python

                return type_validate_python(self.type_, obj)

        from pydantic import root_validator

        def model_validator(*args: Any, **kwargs: Any) -> Any:
            mode = kwargs.get("mode", "after")
            pre = mode == "before"

            def decorator(func: Any) -> Any:
                return root_validator(pre=pre, allow_reuse=True)(func)

            return decorator


__all__ = [
    "PYDANTIC_V2",
    "TypeAdapter",
    "_dump_pydantic_obj",
    "_is_pydantic_type",
    "compat_computed_field",
    "dump_json_safely",
    "model_construct",
    "model_copy",
    "model_dump",
    "model_dump_json",
    "model_fields",
    "model_json_schema",
    "model_rebuild",
    "model_validate",
    "model_validator",
    "parse_as",
    "type_validate_json",
    "type_validate_python",
]


def model_copy(
    model: T, *, update: dict[str, Any] | None = None, deep: bool = False
) -> T:
    """
    Pydantic `model.copy()` (v1) 和 `model.model_copy()` (v2) 的兼容函数。
    """
    if PYDANTIC_V2:
        return model.model_copy(update=update, deep=deep)
    else:
        update_dict = update or {}
        return model.copy(update=update_dict, deep=deep)


def model_construct(model_class: type[T], **kwargs: Any) -> T:
    """
    Pydantic `model_construct` (v2) 与 `construct` (v1) 的兼容函数。
    """
    if PYDANTIC_V2:
        return model_class.model_construct(**kwargs)
    else:
        return model_class.construct(**kwargs)


def model_validate(model_class: type[T], obj: Any) -> T:
    """
    Pydantic 模型验证兼容函数。
    """
    return type_validate_python(model_class, obj)


def model_dump_json(model: BaseModel, **kwargs: Any) -> str:
    """
    Pydantic `model.json()` (v1) 和 `model.model_dump_json()` (v2) 的兼容函数。
    """
    if PYDANTIC_V2:
        return model.model_dump_json(**kwargs)
    return model.json(**kwargs)


if PYDANTIC_V2:
    from pydantic import computed_field as compat_computed_field
else:
    compat_computed_field = property


def model_json_schema(model_class: type[BaseModel], **kwargs: Any) -> dict[str, Any]:
    """
    Pydantic `Model.schema()` (v1) 和 `Model.model_json_schema()` (v2) 的兼容函数。
    """
    if PYDANTIC_V2:
        return model_class.model_json_schema(**kwargs)
    return model_class.schema(by_alias=kwargs.get("by_alias", True))


def _is_pydantic_type(t: Any) -> bool:
    """
    递归检查一个类型注解是否与 Pydantic BaseModel 相关。
    """
    if t is None:
        return False
    origin = get_origin(t)
    if origin:
        return any(_is_pydantic_type(arg) for arg in get_args(t))
    return isinstance(t, type) and issubclass(t, BaseModel)


def _dump_pydantic_obj(obj: Any) -> Any:
    """
    递归地将一个对象内部的 Pydantic BaseModel 实例转换为字典。
    支持单个实例、实例列表、实例字典等情况。
    """
    if isinstance(obj, BaseModel):
        return model_dump(obj)
    if isinstance(obj, list):
        return [_dump_pydantic_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _dump_pydantic_obj(value) for key, value in obj.items()}
    return obj


parse_as = type_validate_python


def dump_json_safely(obj: Any, **kwargs) -> str:
    """
    安全地将可能包含 Pydantic 特定类型 (如 Enum) 的对象序列化为 JSON 字符串。
    """

    def default_serializer(o):
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, Path):
            return str(o.as_posix())
        if isinstance(o, set):
            return list(o)
        if isinstance(o, BaseModel):
            return model_dump(o)
        raise TypeError(
            f"Object of type {o.__class__.__name__} is not JSON serializable"
        )

    return json.dumps(obj, default=default_serializer, **kwargs)


def model_rebuild(model_class: type[BaseModel], **kwargs: Any) -> None:
    """
    Pydantic V1/V2 兼容的前向引用重建函数。
    V2 调用 `model_rebuild()`，V1 调用 `update_forward_refs()`。
    """
    if PYDANTIC_V2:
        model_class.model_rebuild(**kwargs)
    else:
        model_class.update_forward_refs(**kwargs)
