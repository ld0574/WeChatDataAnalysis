"""Bind citation grammars through schema branches for unused model roles only.

This helper is deliberately opt-in. Existing writer code, retained requests and
exhausted stage schemas must never be retroactively changed with this function.
"""
from __future__ import annotations

import json

POLICY = "branch-aware-original-citation-schema-v1"
MAPPINGS = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
SINGLES = ("items", "additionalProperties", "propertyNames", "not", "if", "then", "else", "contains",
    "unevaluatedProperties", "unevaluatedItems", "additionalItems")
BRANCHES = ("allOf", "anyOf", "oneOf", "prefixItems")


def bind(schema, packet):
    sources = packet["sources"]
    if not sources or any(not isinstance(key, str) for key in sources):
        raise ValueError("requires_branch_schema_nonempty_original_source_ids")
    # Break shared schema aliases, preserving all non-citation constraints.
    result = json.loads(json.dumps(schema))
    ids = sorted(sources)
    frames = [key for key in ids if sources[key].get("image_sha256")]

    def visit(node):
        if not isinstance(node, dict):
            return
        for name, child in node.get("properties", {}).items():
            if name in {"citations", "code_citations"}:
                child["items"] = {"type": "string", "enum": ids}
                if name == "citations":
                    child["minItems"] = 1
            elif name == "image":
                child["enum"] = ["", *frames]
        for key in MAPPINGS:
            for child in node.get(key, {}).values():
                visit(child)
        for key in SINGLES:
            child = node.get(key)
            if isinstance(child, list):
                for value in child:
                    visit(value)
            else:
                visit(child)
        for key in BRANCHES:
            for child in node.get(key, []):
                visit(child)

    visit(result)
    return result
