"""Loading and validating the profile JSON Schema."""

import json
from functools import cache

from jsonschema import Draft202012Validator, FormatChecker

from jobfinder.paths import SCHEMA_PATH


@cache
def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(profile: dict) -> list[str]:
    """Return human-readable validation errors; empty list means valid."""
    validator = Draft202012Validator(load_schema(), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(profile), key=lambda e: list(e.absolute_path))
    return [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors]


def resolve(node: dict) -> dict:
    """Follow a local $ref ("#/$defs/...")."""
    while "$ref" in node:
        node = load_schema()["$defs"][node["$ref"].split("/")[-1]]
    return node
