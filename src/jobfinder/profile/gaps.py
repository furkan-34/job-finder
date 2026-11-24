"""Find what is still missing in a profile, and set values by JSON Pointer.

`missing` walks the schema using `x-level`: missing `required` fields must be
answered, missing `recommended` fields are asked but may be skipped (stored
as null), `optional` fields are never asked. Inside objects that carry no
`x-level` (e.g. an experience entry), the object's `required` list decides.
"""

import json

from jobfinder.profile.schema import load_schema, resolve


def missing(profile: dict) -> list[dict]:
    """Return one entry per field the candidate still has to answer."""
    gaps: list[dict] = []
    _walk(load_schema(), profile, "", gaps)
    return gaps


def _level(name: str, prop: dict, parent: dict) -> str:
    if "x-level" in prop:
        return prop["x-level"]
    if "required" not in parent:
        return "recommended"  # e.g. availability: every field is skippable
    return "required" if name in parent["required"] else "optional"


def _walk(schema: dict, data: dict, path: str, gaps: list[dict]) -> None:
    for name, prop in schema.get("properties", {}).items():
        if name == "meta":
            continue
        level = _level(name, prop, schema)
        if level == "optional":
            continue
        sub = f"{path}/{name}"
        node = resolve(prop)
        if name not in data:
            if "properties" in node:
                # A whole section is missing: list its fields instead of the section.
                _walk(node, {}, sub, gaps)
            else:
                gaps.append(_gap(sub, level, prop))
            continue
        value = data[name]
        if isinstance(value, dict) and "properties" in node:
            _walk(node, value, sub, gaps)
        elif isinstance(value, list) and node.get("type") == "array":
            items = resolve(node["items"])
            if not value and node.get("minItems"):
                gaps.append(_gap(sub, level, prop))
            elif "properties" in items:
                for i, item in enumerate(value):
                    _walk(items, item, f"{sub}/{i}", gaps)


def _gap(path: str, level: str, prop: dict) -> dict:
    gap = {"path": path, "level": level, "expects": _describe(prop)}
    if "description" in prop:
        gap["description"] = prop["description"]
    return gap


def _describe(prop: dict):
    """Compact, human-readable shape of the expected value."""
    node = resolve(prop)
    variants = node.get("anyOf") or node.get("oneOf")
    if variants:
        return " | ".join(str(_describe(v)) for v in variants)
    if "const" in node:
        return f"exactly {json.dumps(node['const'])}"
    if "enum" in node:
        return "one of: " + ", ".join(map(str, node["enum"]))
    kind = node.get("type", "any")
    if isinstance(kind, list):
        kind = " | ".join(kind)
    if kind == "array":
        return f"list of ({_describe(node['items'])})"
    if kind == "object":
        fields = ", ".join(node.get("properties", {}))
        return f"object with: {fields}"
    if "pattern" in node:
        return f"{kind} matching {node['pattern']}"
    if "format" in node:
        return f"{kind} ({node['format']})"
    return kind


def set_pointer(doc, pointer: str, value) -> None:
    """Set a value at a JSON Pointer (RFC 6901). Missing parent objects are created (a
    missing list when `-` follows, e.g. /extras/certifications/-); `-` as the last token
    appends to a list."""
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.lstrip("/").split("/")]
    target = doc
    for n, part in enumerate(parts[:-1]):
        if isinstance(target, list):
            target = target[int(part)]
        else:
            target = target.setdefault(part, [] if parts[n + 1] == "-" else {})
    last = parts[-1]
    if isinstance(target, list):
        if last == "-":
            target.append(value)
        else:
            target[int(last)] = value
    else:
        target[last] = value
