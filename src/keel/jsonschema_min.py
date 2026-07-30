"""A tiny, dependency-free JSON-Schema validator (draft-07 subset).

keel validates ``project.yaml`` against ``projects/schema/project.schema.json``.
Rather than take a runtime dependency on ``jsonschema``, we implement exactly the
subset of keywords the schema uses. The function is pure and deterministic:
identical inputs always yield the same ordered list of error strings.

Supported keywords
------------------
``type`` (incl. list-of-types), ``const``, ``enum``, ``required``,
``properties``, ``additionalProperties`` (bool), ``items``, ``minItems``,
``pattern``, ``minLength``, ``minimum``, ``maximum``.

Anything else in a schema is ignored (forward-compatible), so the schema must not
rely on unsupported keywords for enforcement.
"""

from __future__ import annotations

import re
from typing import Any

# JSON-Schema type name -> Python type(s). ``int`` is excluded from "number"
# only conceptually; JSON has no separate int, so we accept both for "number".
_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _type_matches(value: Any, type_name: str) -> bool:
    py = _TYPES.get(type_name)
    if py is None:
        return True  # unknown type name -> do not enforce
    # bool is a subclass of int; keep them distinct for "integer"/"number".
    if type_name in ("integer", "number") and isinstance(value, bool):
        return False
    if type_name == "boolean":
        return isinstance(value, bool)
    return isinstance(value, py)


def validate(instance: Any, schema: dict, path: str = "$") -> list[str]:
    """Return an ordered list of human-readable error strings (empty == valid)."""
    errors: list[str] = []
    _validate(instance, schema, path, errors)
    return errors


def _validate(instance: Any, schema: dict, path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        return

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r} (got {instance!r})")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']!r} (got {instance!r})")

    if "type" in schema:
        types = schema["type"]
        types = [types] if isinstance(types, str) else types
        if not any(_type_matches(instance, t) for t in types):
            errors.append(f"{path}: expected type {'/'.join(types)} (got {_kind(instance)})")
            # If the basic type is wrong, deeper checks would be noisy; stop here.
            return

    if isinstance(instance, str):
        _validate_string(instance, schema, path, errors)
    elif isinstance(instance, list):
        _validate_array(instance, schema, path, errors)
    elif isinstance(instance, dict):
        _validate_object(instance, schema, path, errors)
    elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
        _validate_number(instance, schema, path, errors)


def _validate_string(instance: str, schema: dict, path: str, errors: list[str]) -> None:
    pat = schema.get("pattern")
    if pat is not None and re.search(pat, instance) is None:
        errors.append(f"{path}: {instance!r} does not match pattern {pat!r}")
    min_len = schema.get("minLength")
    if min_len is not None and len(instance) < min_len:
        errors.append(f"{path}: shorter than minLength {min_len}")


def _validate_number(instance: int | float, schema: dict, path: str, errors: list[str]) -> None:
    minimum = schema.get("minimum")
    if minimum is not None and instance < minimum:
        errors.append(f"{path}: {instance!r} is less than minimum {minimum}")
    maximum = schema.get("maximum")
    if maximum is not None and instance > maximum:
        errors.append(f"{path}: {instance!r} is greater than maximum {maximum}")


def _validate_array(instance: list, schema: dict, path: str, errors: list[str]) -> None:
    min_items = schema.get("minItems")
    if min_items is not None and len(instance) < min_items:
        errors.append(f"{path}: fewer than minItems {min_items}")
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for i, item in enumerate(instance):
            _validate(item, item_schema, f"{path}[{i}]", errors)


def _validate_object(instance: dict, schema: dict, path: str, errors: list[str]) -> None:
    for key in schema.get("required", []):
        if key not in instance:
            errors.append(f"{path}: missing required property {key!r}")

    props = schema.get("properties", {})
    for key, subschema in props.items():
        if key in instance:
            child = f"{path}.{key}" if path != "$" else f"$.{key}"
            _validate(instance[key], subschema, child, errors)

    additional = schema.get("additionalProperties", True)
    if additional is False:
        for key in instance:
            if key not in props:
                errors.append(f"{path}: unknown property {key!r}")
    elif isinstance(additional, dict):
        for key, value in instance.items():
            if key not in props:
                child = f"{path}.{key}" if path != "$" else f"$.{key}"
                _validate(value, additional, child, errors)


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__
