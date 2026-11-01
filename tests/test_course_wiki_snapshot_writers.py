import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from test_course_wiki import archive as source_archive, published as source_published
from test_course_wiki_documents import courseware as source_courseware
from test_course_wiki_revision import setup as revision_setup
from test_course_wiki_synthesis import long_plan as source_long_plan, model
from wechat_decrypt_tool import course_library as library
from wechat_decrypt_tool import course_wiki_revision as revision
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool import course_wiki_synthesis as synthesis
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

archive = source_archive
published = source_published
courseware = source_courseware
long_plan = source_long_plan


def retain_frames(store, key):
    evidence = json.loads((store.root / "lessons" / key / "evidence.json").read_bytes())
    for frame in evidence["frames"]:
        Image.new("RGB", (24, 18), "white").save(store.root / frame["path"], "JPEG")
    library.publish(ReadOnlyArchive(store.root))


@pytest.fixture(params=["body", "claims"])
def reused_publication(request, monkeypatch):
    if request.param == "body":
        store, plan = request.getfixturevalue("long_plan")
        key = request.getfixturevalue("published")[1]
        retain_frames(store, key)

        def fixed_answer(stage, context):
            if "fixed_question" not in context:
                return None
            ref = next(iter(context["sources"]))
            return json.dumps(
                {
                    "quiz": {
                        "answer": "Synthetic supported answer.",
                        "explanation": "Synthetic retained condition.",
                        "criteria": ["Identify the condition."],
                        "citations": [ref],
                    },
                    "pitfalls": [{"text": "Synthetic limitation.", "citations": [ref]}],
                    "gaps": [],
                }
            )

        model(monkeypatch, fixed_answer)
        result = synthesis.generate(
            ReadOnlyArchive(store.root), plan, fixed_assessments=True
        )
        field = "unchanged_body_job"
    else:
        store, key = request.getfixturevalue("published")
        retain_frames(store, key)
        store, parent, _ = revision_setup((store, key), monkeypatch)
        result = revision.revise(
            ReadOnlyArchive(store.root), parent, source_scoped_additions=True
        )
        field = "unchanged_claim_job"
    primary = store.root / "wiki/jobs" / result["id"]
    recipe = json.loads((primary / "manifest.json").read_bytes())["recipe"]
    dependency = store.root / "wiki/jobs" / recipe["extra"][field]
    return store.root, primary, dependency


def test_reused_unpublished_writer_checkpoints_survive_isolated_restore(
    reused_publication, tmp_path
):
    root, primary, dependency = reused_publication
    before = {p.relative_to(root): p.read_bytes() for p in dependency.rglob("*.json")}
    assert before
    backup = snapshot.create(root, tmp_path / "backup", reserve_gib=0)
    restored = snapshot.restore(backup, tmp_path / "restored", reserve_gib=0)
    for relative, content in before.items():
        assert (restored / relative).read_bytes() == content
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(root),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(process.stdout)["original_root_access_attempts"] == 0


@pytest.mark.parametrize(
    "mutation", ["manifest", "missing_stage", "changed_stage", "primary"]
)
def test_self_consistent_snapshot_list_cannot_hide_reused_writer_loss(
    reused_publication, tmp_path, mutation
):
    root, primary, dependency = reused_publication
    backup = snapshot.create(root, tmp_path / "backup", reserve_gib=0)
    primary = backup / primary.relative_to(root)
    dependency = backup / dependency.relative_to(root)
    stage = next(
        p
        for p in dependency.glob("*.json")
        if p.stem.startswith(("chapter-", "repair-"))
    )
    target = (
        primary / "manifest.json"
        if mutation == "primary"
        else dependency / "manifest.json"
        if mutation == "manifest"
        else stage
    )
    manifest_path = backup / snapshot.MANIFEST
    manifest = json.loads(manifest_path.read_bytes())
    row = next(
        r
        for r in manifest["files"]
        if r["path"] == target.relative_to(backup).as_posix()
    )
    if mutation == "changed_stage":
        value = json.loads(target.read_bytes())
        value["data"] = {"changed": "Synthetic corruption."}
        atomic_json(target, value)
        row.update(bytes=target.stat().st_size, sha256=snapshot.file_hash(target))
    else:
        target.unlink()
        manifest["files"].remove(row)
    atomic_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="snapshot_writer_"):
        snapshot.verify(backup)
