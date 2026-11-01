"""Offline, evidence-bound graph comparison for an existing local model trial."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from .course_knowledge import validate_note
from .course_model_trial import digest


def flags(edge):
    found = []
    if edge["from"] == edge["to"]:
        found.append("self_edge")
    if re.search(r"无.*关系|没有.*关系|不.*相关|未.*关系", edge["relation"]):
        found.append("negative_label_candidate")
    return found


def build_data(plan, results, binding):
    if digest(plan) != binding["plan_sha256"]:
        raise ValueError("Trial plan changed")
    if set(results) != set(binding["models"]):
        raise ValueError("Trial model set changed")
    models = list(binding["models"])
    samples = []
    totals = {
        m: {
            "nodes": 0,
            "edges": 0,
            "self_edges": 0,
            "negative_label_candidates": 0,
            "extra_call_windows": 0,
        }
        for m in models
    }
    for case in plan["samples"]:
        evidence = case["evidence"] + case["visuals"]
        if len(case["original_ids"]) != len(evidence):
            raise ValueError("Evidence source binding is incomplete")
        if [e["id"] for e in evidence] != list(range(1, len(evidence) + 1)):
            raise ValueError("Evidence IDs differ from trial numbering")
        bound = {
            str(e["id"]): {
                **e,
                "original_id": original,
                "kind": "transcript" if index < len(case["evidence"]) else "ocr",
            }
            for index, (e, original) in enumerate(
                zip(evidence, case["original_ids"], strict=True)
            )
        }
        sample = {
            "id": case["id"],
            "title": case["title"],
            "video": case["video"],
            "category": case["category"],
            "window": case["window"],
            "input_sha256": digest(case),
            "evidence": bound,
            "models": {},
        }
        requests = []
        for model in models:
            result = results[model]["course:" + case["id"]]
            if result["status"] != "ok" or result["model"] != model:
                raise ValueError("Only successful bound trial results can be graphed")
            if result["input_sha256"] != digest(case):
                raise ValueError("Result does not belong to frozen evidence")
            if not result["attempts"]:
                raise ValueError("Trial request provenance is missing")
            requests.append(result["attempts"][0]["request_sha256"])
            note = validate_note(result["output"], set(range(1, len(evidence) + 1)))
            nodes = [
                {**c, "id": "node-" + str(i)} for i, c in enumerate(note["concepts"])
            ]
            names = [c["name"] for c in nodes]
            edges = []
            for index, edge in enumerate(note["relations"]):
                annotated = {
                    **edge,
                    "id": "edge-" + str(index),
                    "flags": flags(edge),
                    "source": [n["id"] for n in nodes if n["name"] == edge["from"]],
                    "target": [n["id"] for n in nodes if n["name"] == edge["to"]],
                }
                if len(annotated["source"]) != 1 or len(annotated["target"]) != 1:
                    annotated["flags"].append("ambiguous_endpoint")
                edges.append(annotated)
            metrics = {
                "nodes": len(nodes),
                "edges": len(edges),
                "self_edges": sum("self_edge" in e["flags"] for e in edges),
                "negative_label_candidates": sum(
                    "negative_label_candidate" in e["flags"] for e in edges
                ),
                "extra_call_windows": int(len(result["attempts"]) > 1),
            }
            for key, value in metrics.items():
                totals[model][key] += value
            sample["models"][model] = {
                "nodes": nodes,
                "edges": edges,
                "summary": note["summary"],
                "metrics": metrics,
                "calls": len(result["attempts"]),
                "duplicate_names": len(names) != len(set(names)),
            }
        if len(set(requests)) != 1:
            raise ValueError("Paired first prompts differ")
        samples.append(sample)
    return {
        "version": 1,
        "plan_sha256": digest(plan),
        "results_sha256": digest(results),
        "models": models,
        "model_digests": binding["models"],
        "samples": samples,
        "totals": totals,
        "semantic_accuracy_verified": False,
        "source_deletion_allowed": False,
        "production_changed": False,
    }


def render(data):
    template = (Path(__file__).parent / "resources/course_model_graph.html").read_text(
        "utf-8"
    )
    payload = (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    if template.count("__TRIAL_GRAPH_DATA__") != 1:
        raise ValueError("Graph template must have one data slot")
    return template.replace("__TRIAL_GRAPH_DATA__", payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    args = parser.parse_args(argv)
    folder = args.trial.resolve()
    inputs = {
        name: json.loads((folder / (name + ".json")).read_text("utf-8"))
        for name in ("plan", "results", "binding")
    }
    data = build_data(**inputs)
    output = folder / "graph-comparison.html"
    output.write_text(render(data), encoding="utf-8")
    receipt = {
        "version": 1,
        "samples": len(data["samples"]),
        "totals": data["totals"],
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "plan_sha256": data["plan_sha256"],
        "results_sha256": data["results_sha256"],
        "semantic_accuracy_verified": False,
        "source_deletion_allowed": False,
        "production_changed": False,
    }
    (folder / "graph-comparison-validation.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
