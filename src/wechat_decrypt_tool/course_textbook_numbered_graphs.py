"""Lossless numbered graph decoding for original, previously unused writers.

All node labels are explicitly required. Endpoints can only name those nodes;
the ordinary graph, source and semantic validators still decide acceptance.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
import re

from . import course_textbook_corpus_index_review as original

fresh, writing, whole = original.fresh, original.writing, original.whole
digest = original.digest
POLICY = "original-first-role-required-numbered-graph-labels-v1"
CONTRACT = "first-numbered-graph-format.json"
NOTICE = """
解释图先声明 node_count（2–16）。nodes 是必填编号键 n0 到 n(node_count-1) 的对象，
每个键的值为该节点的完整标签；edges.from/to 只能引用这些实际编号，每个节点都必须参与连线。
不得创造原文不支持的含义、关系或标签；没有图不能冒充图文检查通过。
仍按实际 paragraph_count 输出完整正文，覆盖全部有效教学；图格式不证明事实或知识覆盖。
"""
SAFE_GRAPH_ERRORS = frozenset({"textbook_diagram_unknown_or_unconnected_node",
    "textbook_diagram_duplicate_node", "textbook_diagram_empty_label"})


def require(ok, code):
    if not ok:
        raise ValueError("requires_numbered_graph_" + code)


def binding():
    return {"policy": POLICY, "module_sha256": whole.sha(Path(__file__).read_bytes()),
        "original_module_sha256": whole.sha(Path(original.__file__).read_bytes())}


def graph_schema(schema):
    """Replace only the representation of a node array and bind its endpoints."""
    require(schema["properties"]["nodes"]["type"] == "array", "original_graph_shape")
    label = copy.deepcopy(schema["properties"]["nodes"]["items"]["properties"]["label"])
    label.update(minLength=1, pattern=r"\S")
    branches = []
    for count in range(2, 17):
        branch = copy.deepcopy(schema)
        fields = branch["properties"]
        keys = [f"n{i}" for i in range(count)]
        fields["nodes"] = writing.object_schema({key: copy.deepcopy(label) for key in keys})
        for endpoint in ("from", "to"):
            fields["edges"]["items"]["properties"][endpoint] = {"type": "string", "enum": keys}
        branch["properties"] = {"node_count": {"const": count}, **fields}
        branch["required"] = ["node_count", *branch["required"]]
        branches.append(branch)
    return {"oneOf": branches}


def compile_graph(raw):
    value = copy.deepcopy(raw)
    count = value.pop("node_count", None)
    require(type(count) is int and 2 <= count <= 16, "actual_node_count")
    keys = [f"n{i}" for i in range(count)]
    require(isinstance(value.get("nodes"), dict) and set(value["nodes"]) == set(keys), "all_actual_node_labels")
    labels = value["nodes"]
    value["nodes"] = [{"id": key, "label": labels[key]} for key in keys]
    require(all(edge[field] in labels for edge in value["edges"] for field in ("from", "to")),
        "endpoints_within_actual_nodes")
    return value


def section_schema(schema, packet, pending):
    result = original.tagged_schema(schema, packet, pending)

    def visit(node):
        if isinstance(node, dict):
            diagrams = node.get("properties", {}).get("diagrams")
            if diagrams is not None:
                diagrams["items"] = graph_schema(diagrams["items"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(result)
    return result


def candidate(raw, ids, working, inventory, scope):
    value = copy.deepcopy(raw)
    translated = []
    for index, graph in enumerate(value.get("diagrams", [])):
        compiled = compile_graph(graph)
        value["diagrams"][index] = compiled
        translated.append({"index": index, "nodes": len(compiled["nodes"]), "edges": len(compiled["edges"]),
            "raw_graph_sha256": digest(graph), "compiled_graph_sha256": digest(compiled),
            "labels_edges_and_meanings_changed": False})
    try:
        result, changes = original.tagged_candidate(value, ids, working, inventory, scope)
    except ValueError as exc:
        if type(exc) is ValueError and str(exc) in SAFE_GRAPH_ERRORS:
            # The old writer retains requires_* reasons for its second original
            # structural attempt. Only these known constants may cross that boundary.
            raise ValueError("requires_numbered_graph_" + str(exc)) from None
        raise
    return result, {"original_citation_metadata": changes, "numbered_graph_translations": translated,
        "graph_semantics_changed_by_adapter": False}


@contextmanager
def format_scope():
    previous = fresh.section_schema, fresh.citation_candidate
    prompts = {role: writing.composition.PROMPTS[role]
        for role in (writing.author.WRITE_PROMPT, writing.author.REPAIR_PROMPT)}
    fresh.section_schema, fresh.citation_candidate = section_schema, candidate
    try:
        for role, prompt in prompts.items():
            writing.composition.PROMPTS[role] = prompt + original.NOTICE + NOTICE
        yield
    finally:
        fresh.section_schema, fresh.citation_candidate = previous
        writing.composition.PROMPTS.update(prompts)


def job_type(base, contract, *, read_only):
    stage = next(cls.stage for cls in base.__mro__ if cls.__name__ == "FreshJob")

    class NumberedGraphWriter(base):
        def __init__(self, *args):
            super().__init__(*args)
            require(contract["format"] == binding() and contract["writer"] == self.key
                and whole.read(self.root / CONTRACT) == contract, "same_first_original_graph_format")
            self.reader.paths.add(self.root / CONTRACT)

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[2] in {"write", "repair"}:
                with format_scope():
                    return stage(self, name, prompt, schema, context, validate, **kwargs)
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    return NumberedGraphWriter
