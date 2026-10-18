"""A deliberately small JSON Schema validator: just the keywords the tool schemas use.

Supported: type (string or list), enum, minimum, maximum, minLength, maxLength, pattern, properties,
required, additionalProperties (false only). Anything else in a schema is ignored, not enforced.
"""
from __future__ import annotations

import re
from typing import Any

_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def validate(value: Any, schema: dict[str, Any], path: str = "arguments") -> list[str]:
    errors: list[str] = []
    wanted = schema.get("type")
    if wanted is not None:
        allowed = wanted if isinstance(wanted, list) else [wanted]
        if not any(_TYPES[t](value) for t in allowed):
            return [f"{path}: expected {' or '.join(allowed)}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {', '.join(map(str, schema['enum']))}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: must be at least {schema['minLength']} characters")
        if len(value) > schema.get("maxLength", len(value)):
            errors.append(f"{path}: must be at most {schema['maxLength']} characters")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            errors.append(f"{path}: does not match the required format")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be at most {schema['maximum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required property '{name}'")
        for name, item in value.items():
            if name in props:
                errors.extend(validate(item, props[name], f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property '{name}'")
    return errors
