"""
Pydantic V1 & V2 兼容层模块

为 Pydantic V1 与 V2 版本提供统一的便捷函数与类，
包括 model_dump, model_copy, model_json_schema, parse_as 等。
"""

from typing import Any, TypeVar, get_args, get_origin

from nonebot.compat import PYDANTIC_V2, model_dump
from pydantic import VERSION, BaseModel

T = TypeVar("T", bound=BaseModel)
V = TypeVar("V")


__all__ = [
    "PYDANTIC_V2",
    "_dump_pydantic_obj",
    "_is_pydantic_type",
    "compat_computed_field",
    "model_copy",
    "model_dump",
    "model_json_schema",
    "parse_as",
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
    else:
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


def parse_as(type_: type[V], obj: Any) -> V:
    """
    一个兼容 Pydantic V1 的 parse_obj_as 和V2的TypeAdapter.validate_python 的辅助函数。
    """
    if VERSION.startswith("1"):
        from pydantic import parse_obj_as

        return parse_obj_as(type_, obj)
    else:
        from pydantic import TypeAdapter  # type: ignore

        return TypeAdapter(type_).validate_python(obj)
