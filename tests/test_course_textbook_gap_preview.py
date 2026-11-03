import copy
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_textbook_gap_preview as preview
from wechat_decrypt_tool.course_library import canonical
from test_course_textbook_gap_plan import inputs
from test_course_textbook_gap_write import candidate, packet, review


def test_only_clean_draft_and_counts_cross_http_boundary(tmp_path):
    value = inputs()
    parent = value["parent"]
    path = tmp_path / "wiki/whole-lesson-repair-jobs" / parent
    path.mkdir(parents=True)
    preview.writer.atomic_json(path / "corrected-reading.json", value["book"])
    client = TestClient(preview.create_app(tmp_path, parent))
    page = client.get("/")
    assert page.status_code == 200
    assert "尚未验收" in page.text
    assert "Existing explanation" in page.text
    assert "complete_sources" not in page.text and "gap1" not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert client.get("/status").json()["quality_complete"] is False
    assert client.get("/manifest.json").status_code == 404
    assert client.get("/../../private-authoring-plan.json").status_code == 404


def test_partial_projection_preserves_unwritten_units_and_checks_cache(tmp_path, monkeypatch):
    value, plan = packet()
    parent = value["parent"]
    parent_folder = tmp_path / "wiki/whole-lesson-repair-jobs" / parent
    parent_folder.mkdir(parents=True)
    preview.writer.atomic_json(parent_folder / "corrected-reading.json", value["book"])
    binding = {"bound": True}
    monkeypatch.setattr(preview.writer, "load_plan", lambda *args: (value, plan, binding))
    recipe = {"packet": {"sources": value["sources"]},
              "extra": {"plan": "d" * 32, "plan_bindings": binding, "parent_bindings": value["bindings"]}}
    key = preview.sha(canonical(recipe))[:32]
    folder = tmp_path / "wiki/textbook-gap-write-jobs" / key
    folder.mkdir(parents=True)
    save = preview.writer.atomic_json
    save(folder / "manifest.json", {"id": key, "recipe": recipe})
    save(tmp_path / "wiki/textbook-gap-write-current.json", {"parent": parent, "job": key, "stage": "writing"})
    unit = plan["units"][0]
    generated = candidate(unit)
    assessment = review(generated, unit)
    proof = {"unit": unit["unit"], "candidate": generated, "review": assessment,
             "checks_passed": True, "reader_publication_allowed": False}
    save(folder / (unit["unit"] + "-private-result.json"), proof)
    for suffix, data in [("-write", generated), ("-review-0", assessment)]:
        save(folder / (unit["unit"] + suffix + ".json"), {"data": data, "data_sha256": preview.sha(canonical(data))})
    book, status = preview.projection(tmp_path, parent)
    assert status["units_written"] == 1 and status["total_units"] == 2
    assert len(book["chapters"][0]["sections"][0]["paragraphs"]) == 2
    assert book["quiz"][0] == value["book"]["quiz"][0]
    assert "source_keys" not in str(book)

    # Finishing prose must switch to the real review phase, not stay "writing".
    second = plan["units"][1]
    second_value = candidate(second)
    second_review = review(second_value, second)
    save(folder / (second["unit"] + "-private-result.json"), {
        "unit": second["unit"], "candidate": second_value, "review": second_review,
        "checks_passed": True, "reader_publication_allowed": False})
    for suffix, data in [("-write", second_value), ("-review-0", second_review)]:
        save(folder / (second["unit"] + suffix + ".json"), {
            "data": data, "data_sha256": preview.sha(canonical(data))})
    client = TestClient(preview.create_app(tmp_path, parent))
    state = client.get("/status").json()
    assert state["state"] == "checking_sources"
    assert state["source_inputs_checked"] == 0
    assert state["source_inputs_total"] == len(value["sources"])
    coverage = {"sources": {k: {"kind": "non_teaching", "reason": "Private review text"}
                            for k in value["sources"]}}
    coverage["sources"]["gap1"]["kind"] = "missing"
    cache = {"data": coverage, "data_sha256": preview.sha(canonical(coverage))}
    save(folder / "whole-sources-000.json", cache)
    state = client.get("/status").json()
    assert state["state"] == "checking_article"
    assert state["source_inputs_checked"] == len(value["sources"])
    assert state["source_coverage_issues"] == 1
    assert state["quality_complete"] is False
    assert "Private review text" not in client.get("/").text
    assert "Private review text" not in str(state)
    save(tmp_path / "wiki/textbook-gap-write-current.json", {
        "parent": parent, "job": key, "stage": "whole-reading", "state": "stopped"})
    assert client.get("/status").json()["state"] == "stopped"
    assert "当前核查任务已停止" in client.get("/").text
    cache["data"]["sources"]["gap1"]["kind"] = "non_teaching"
    save(folder / "whole-sources-000.json", cache)
    assert client.get("/status").status_code == 503
    save(folder / "whole-sources-000.json", {
        "data": coverage, "data_sha256": preview.sha(canonical(coverage))})

    changed = copy.deepcopy(generated)
    changed["paragraphs"][0]["text"] += "Changed"
    proof["candidate"] = changed
    save(folder / (unit["unit"] + "-private-result.json"), proof)
    assert TestClient(preview.create_app(tmp_path, parent)).get("/").status_code == 503


def test_authored_markdown_keeps_code_indentation_and_renders_emphasis():
    book = inputs()["book"]
    book["chapters"][0]["sections"][0]["paragraphs"] = [
        '**Explain first.**\n\n```python\nif ready:\n    run("<value>")\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |']
    output = preview.reading_html(book)
    assert '<strong>Explain first.</strong>' in output
    assert '<pre><code class="language-python">' in output
    assert '    run(&quot;&lt;value&gt;&quot;)' in output or '    run("&lt;value&gt;")' in output
    assert '<table>' in output
    assert 'white-space:pre-wrap' in output
    assert '```' not in output


def test_markdown_never_loads_images_or_executes_active_html():
    book = inputs()["book"]
    book["chapters"][0]["sections"][0]["paragraphs"] = [
        '![Source image](https://external.invalid/private.png)\n\n<script>run()</script>\n\n'
        '[bad](javascript:run) [relative](private-evidence.json) [valid](https://example.invalid)']
    class Tags(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags, self.links = [], []
        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
            if tag == 'a':
                self.links.extend(v for k, v in attrs if k == 'href')
    parser = Tags()
    parser.feed(preview.reading_html(book))
    assert not {'img', 'script', 'iframe'} & set(parser.tags)
    assert 'javascript:run' not in parser.links
    assert 'private-evidence.json' not in parser.links
    assert 'https://example.invalid' in parser.links
