"""Real note search must expose matches beyond the first response page."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical, create_app
from wechat_decrypt_tool.course_lesson_exports import digest


def published_search_client(tmp_path):
    root = tmp_path / "library"
    (root / "objects").mkdir(parents=True)
    rows = []
    for course, count in (("course-a", 58), ("course-b", 3)):
        value = {
            "sections": [
                {
                    "id": f"section-{i}",
                    "title": f"Example {i}",
                    "start": i * 150,
                    "summary": "Shared retrieval topic.",
                    "concepts": [],
                }
                for i in range(count)
            ]
        }
        raw = canonical(value)
        sha = digest(raw)
        (root / "objects" / (sha + ".json")).write_bytes(raw)
        rows.append(
            {"id": course, "state": "draft_available", "title": course, "artifact": sha}
        )
    atomic_json(root / "catalog.json", {"lessons": rows})
    return TestClient(create_app(SimpleNamespace(root=tmp_path)))


def test_all_ranked_matches_reachable_without_duplicates_and_filter_preserved(tmp_path):
    client = published_search_client(tmp_path)
    offset, collected = 0, []
    while offset is not None:
        page = client.get(
            "/library/api/search",
            params={"q": "shared retrieval", "video": "course-a", "offset": offset},
        ).json()
        assert page["total"] == 58
        assert page["offset"] == offset
        assert len(page["items"]) <= 20
        assert all(item["video"] == "course-a" for item in page["items"])
        collected.extend(item["section"] for item in page["items"])
        offset = page["next_offset"]
    assert collected == [f"section-{i}" for i in range(58)]
    assert len(collected) == len(set(collected))
    unfiltered = client.get(
        "/library/api/search", params={"q": "shared retrieval"}
    ).json()
    assert unfiltered["total"] == 61


def test_empty_and_out_of_range_pages_are_terminal_and_sizes_bounded(tmp_path):
    client = published_search_client(tmp_path)
    for params in (
        {"q": "missing keyword"},
        {"q": "shared retrieval", "offset": 9999},
    ):
        page = client.get("/library/api/search", params=params).json()
        assert page["items"] == []
        assert page["next_offset"] is None
    page = client.get(
        "/library/api/search",
        params={"q": "shared retrieval", "offset": -1, "limit": 999},
    ).json()
    assert page["offset"] == 0 and page["limit"] == 100
    assert len(page["items"]) == page["total"] == 61
