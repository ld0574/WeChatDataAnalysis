"""Offline cross-course navigation over independently checked lesson exports.

Only exactly equal concept names share a node. Occurrences retain their own
explanations, lesson, note window and evidence; name equality is not a semantic
equivalence claim. This derived view never writes the production database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time

from .course_knowledge import atomic_json
from .course_lesson_exports import digest, validate_exports
from .course_video_visual_audit import ReadOnlyArchive


def identity(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def build_graph(store):
    """Use a full canonical-inventory gate, preserving all deferred lessons."""
    gate = validate_exports(store)
    nodes, edges, evidence, lessons = {}, {}, {}, []
    node_occurrences, edge_occurrences = set(), set()
    deferred = list(gate["errors"])
    for checked in gate["completed"]:
        store.checkpoint()
        key = checked["video"]
        try:
            raw = (store.root / "lessons" / key / "evidence.json").read_bytes()
        except OSError:
            deferred.append(
                {"video": key, "code": "export_unavailable_after_validation"}
            )
            continue
        if digest(raw) != checked["files_sha256"]["evidence.json"]:
            deferred.append({"video": key, "code": "export_changed_after_validation"})
            continue
        payload = json.loads(raw)
        lesson = {
            "video": key,
            "title": payload["video"]["title"],
            "has_audio_gaps": checked["has_audio_gaps"],
            "evidence_sha256": digest(raw),
            "rows_sha256": checked["rows_sha256"],
            "note_windows": checked["note_windows"],
            "audio_source": "recovered_gaps"
            if checked["has_audio_gaps"]
            else "alternate_recording"
            if payload.get("audio_recovery")
            else "original",
            "audio_notice": "原音轨有缺失；仅展示独立恢复的可读区间，缺口未填补。"
            if checked["has_audio_gaps"]
            else "原音轨损坏；语音来自替代录制版本，不代表原音轨完整。"
            if payload.get("audio_recovery")
            else "",
        }
        lessons.append(lesson)
        source_rows = {
            row["id"]: {
                "kind": "speech",
                "start": row["start"],
                "end": row["end"],
                "text": row["text"],
            }
            for row in payload["segments"]
        }
        source_rows.update(
            {
                -row["id"]: {
                    "kind": "frame",
                    "start": row["time"],
                    "end": row["time"],
                    "text": row["description"],
                }
                for row in payload["frames"]
            }
        )

        def cite(ids):
            refs = []
            for number in sorted(set(ids)):
                ref = key + ":" + str(number)
                evidence[ref] = {"video": key, "number": number, **source_rows[number]}
                refs.append(ref)
            return refs

        for window, note in enumerate(payload["notes"]):
            for claim in note["concepts"]:
                node_id = identity(claim["name"])
                node = nodes.setdefault(
                    node_id,
                    {"id": node_id, "name": claim["name"], "occurrences": []},
                )
                occurrence = {
                    "video": key,
                    "window": window,
                    "explanation": claim["explanation"],
                    "evidence": cite(claim["evidence"]),
                }
                signature = (
                    node_id,
                    key,
                    window,
                    claim["explanation"],
                    tuple(occurrence["evidence"]),
                )
                if signature not in node_occurrences:
                    node_occurrences.add(signature)
                    node["occurrences"].append(occurrence)
            for claim in note["relations"]:
                edge_id = identity([claim["from"], claim["to"], claim["relation"]])
                edge = edges.setdefault(
                    edge_id,
                    {
                        "id": edge_id,
                        "from": identity(claim["from"]),
                        "to": identity(claim["to"]),
                        "relation": claim["relation"],
                        "occurrences": [],
                    },
                )
                occurrence = {
                    "video": key,
                    "window": window,
                    "evidence": cite(claim["evidence"]),
                }
                signature = (edge_id, key, window, tuple(occurrence["evidence"]))
                if signature not in edge_occurrences:
                    edge_occurrences.add(signature)
                    edge["occurrences"].append(occurrence)
    # A view can describe separately observed lessons, never a single DB instant.
    if (
        digest((store.root / "inventory-unique.json").read_bytes())
        != gate["inventory_sha256"]
    ):
        raise RuntimeError("Canonical inventory changed while building the graph")
    for node in nodes.values():
        node["lesson_count"] = len({x["video"] for x in node["occurrences"]})
    return {
        "policy": "exact-name-cross-course-concepts-v1",
        "updated": time.time(),
        "scope": "full_canonical_audio_inventory",
        "observation": gate["observation"],
        "inventory_sha256": gate["inventory_sha256"],
        "requested_lessons": gate["requested"],
        "excluded_noneligible_inventory_rows": gate[
            "excluded_noneligible_inventory_rows"
        ],
        "lessons": sorted(lessons, key=lambda x: x["video"]),
        "deferred": sorted(deferred, key=lambda x: x["video"]),
        "full_inventory_graph_built": not deferred,
        "concepts": sorted(nodes.values(), key=lambda x: (x["name"], x["id"])),
        "relations": sorted(edges.values(), key=lambda x: x["id"]),
        "evidence": evidence,
        "semantic_equivalence_verified": False,
        "semantic_accuracy_verified": False,
        "complete_visual_coverage": False,
        "audio_coverage_verified": False,
        "source_deletion_allowed": False,
    }


def render_html(graph):
    # Never interpolate course strings into executable JS or HTML markup.
    data = (
        json.dumps(graph, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return _HTML.replace("__COURSE_GRAPH_DATA__", data)


def write_graph(store):
    graph = build_graph(store)
    store.checkpoint()
    atomic_json(store.root / "corpus-graph.json", graph)
    path = store.root / "corpus-graph.html"
    temporary = path.with_suffix(".html.tmp")
    temporary.write_text(render_html(graph), encoding="utf-8")
    temporary.replace(path)
    return graph


_HTML = r"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>跨课程学习图谱</title>
<style>
body{font:16px/1.7 system-ui,sans-serif;margin:32px auto;padding:0 20px;max-width:1200px;color:#222;background:#f7f6f2}
input,select,button{font:inherit;padding:6px;margin:4px}button{cursor:pointer}a{color:#176743}
label{display:inline-block;max-width:100%}select,input[type=search]{max-width:100%;box-sizing:border-box}
main{display:grid;grid-template-columns:minmax(240px,1fr) 2fr;gap:24px}section,aside{min-width:0}
article{border-bottom:1px solid #ccc;padding:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}
#matches{max-height:65vh;overflow:auto;padding-right:10px}#matches button{display:block;text-align:left;width:calc(100% - 8px);overflow-wrap:anywhere}h2,h3{overflow-wrap:anywhere}
.notice{border-left:3px solid #8a6e36;padding-left:12px}@media(max-width:700px){main{display:block}}
</style>
<h1>跨课程学习图谱</h1>
<p id="status" role="status"></p>
<p class="notice">仅将完全相同的概念名称分组，同名不代表含义相同；不同课程的解释分别保留。模型内容仍需核对。截图为抽样，尚不能据此删除原视频。</p>
<label>搜索概念或解释 <input id="query" type="search"></label>
<label>课程 <select id="lesson"><option value="">全部已收录课程</option></select></label>
<label><input id="shared" type="checkbox">只看跨课程同名概念</label>
<main><aside><h2>概念</h2><p id="count" aria-live="polite"></p><nav id="matches" aria-label="概念列表"></nav><button id="more" hidden>再显示 50 个</button></aside>
<section id="details" aria-live="polite"><h2>选择一个概念</h2><p>查看各课程的解释、关系及来源文字。</p></section></main>
<details><summary>范围与未完成课程</summary><pre id="scope"></pre></details>
<script id="graph-data" type="application/json">__COURSE_GRAPH_DATA__</script>
<script>
'use strict';
const graph=JSON.parse(document.getElementById('graph-data').textContent),$=id=>document.getElementById(id);
const nodes=new Map(graph.concepts.map(x=>[x.id,x])),lessons=new Map(graph.lessons.map(x=>[x.video,x]));
const adjacent=new Map();for(const edge of graph.relations){for(const id of new Set([edge.from,edge.to])){if(!adjacent.has(id))adjacent.set(id,[]);adjacent.get(id).push(edge)}}
function element(tag,text,parent){const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(parent)parent.append(x);return x}
function link(text,href,parent){const x=element('a',text,parent);x.href=href;return x}
function courseName(id){return lessons.get(id)?.title||id}
for(const x of graph.lessons){const o=element('option',x.title,$('lesson'));o.value=x.video}
$('status').textContent='收录 '+graph.lessons.length+'/'+graph.requested_lessons+' 门课程，'+graph.concepts.length+' 个同名概念组，'+graph.relations.length+' 种关系。'+(graph.full_inventory_graph_built?'结构化图谱范围已齐全；完整归档仍须单独验收。':'尚有 '+graph.deferred.length+' 门课程未完成。')+' 生成时间：'+new Date(graph.updated*1000).toLocaleString();
$('scope').textContent='仅收录通过完整摘要、窗口引用与逐课导出一致性检查的课程。不同课程分别观察，不是数据库同一时刻的快照。音频完整性、视觉语义与模型正确性须另行校验。\n'+graph.deferred.map(x=>x.video+' · '+x.code).join('\n');
let limit=50,filtered=[];
function occurrences(items){return items.filter(x=>!$('lesson').value||x.video===$('lesson').value)}
function citations(refs,parent){for(const ref of refs){const e=graph.evidence[ref],box=element('details',undefined,parent);element('summary',courseName(e.video)+' · '+(e.kind==='speech'?'语音':'画面')+' '+e.start.toFixed(1)+' 秒 · 证据 '+e.number,box);element('pre',e.text||'（无识别文字）',box);link('本课离线复核页','lessons/'+e.video+'/review.html#e'+e.number,box);element('span',' · ',box);link('结构化来源','lessons/'+e.video+'/evidence.json',box)}}
function choose(id){const n=nodes.get(id),target=$('details');target.replaceChildren();element('h2',n.name,target);element('p','出现于 '+n.lesson_count+' 门课程；解释保留各自来源。',target);
for(const o of occurrences(n.occurrences)){const a=element('article',undefined,target);element('h3',courseName(o.video)+' · 摘要窗口 '+(o.window+1),a);if(lessons.get(o.video).audio_notice)element('p',lessons.get(o.video).audio_notice,a);element('p',o.explanation,a);citations(o.evidence,a)}
element('h3','关联概念',target);let found=0;
for(const e of adjacent.get(id)||[]){const uses=occurrences(e.occurrences);if(!uses.length)continue;found++;const a=element('article',undefined,target),p=element('p',undefined,a);for(const [index,nodeid]of [e.from,e.to].entries()){if(index)element('span',' → '+e.relation+' → ',p);const b=element('button',nodes.get(nodeid).name,p);b.onclick=()=>choose(nodeid)}for(const o of uses){element('p',courseName(o.video)+' · 摘要窗口 '+(o.window+1),a);citations(o.evidence,a)}}
if(!found)element('p','当前范围没有已保存的关系。',target)}
function draw(){const query=$('query').value.toLocaleLowerCase();filtered=graph.concepts.filter(n=>(!$('shared').checked||n.lesson_count>1)&&occurrences(n.occurrences).length&&(n.name.toLocaleLowerCase().includes(query)||occurrences(n.occurrences).some(o=>o.explanation.toLocaleLowerCase().includes(query))));$('matches').replaceChildren();for(const n of filtered.slice(0,limit)){const b=element('button',n.name+' · '+n.lesson_count+' 门课程',$('matches'));b.onclick=()=>choose(n.id)}$('count').textContent='找到 '+filtered.length+' 个，显示 '+Math.min(limit,filtered.length)+' 个';$('more').hidden=filtered.length<=limit}
for(const id of ['query','lesson','shared'])$(id).addEventListener('input',()=>{limit=50;$('details').replaceChildren();element('h2','选择一个概念',$('details'));draw()});$('more').onclick=()=>{limit+=50;draw()};draw();
</script></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    graph = write_graph(ReadOnlyArchive(args.root))
    print(
        json.dumps(
            {
                "requested_lessons": graph["requested_lessons"],
                "included_lessons": len(graph["lessons"]),
                "deferred_lessons": len(graph["deferred"]),
                "concepts": len(graph["concepts"]),
                "relations": len(graph["relations"]),
                "evidence": len(graph["evidence"]),
                "full_inventory_graph_built": graph["full_inventory_graph_built"],
                "source_deletion_allowed": False,
            }
        )
    )
    return 2 if args.require_complete and not graph["full_inventory_graph_built"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
