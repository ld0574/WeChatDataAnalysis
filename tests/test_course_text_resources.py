import hashlib
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app
from wechat_decrypt_tool.course_text_resources import (
    decode_text,
    index_text_resources,
    text_chunks,
)


def test_plain_config_masks_unquoted_quoted_multiline_and_incomplete_credentials():
    raw = b'PORT=1234\nexport OPENAI_API_KEY=synthetic_unquoted_value # trailing\nPASSWORD="one\\"two"\nPRIVATE_KEY="""multi\nsynthetic_private_material\nend"""\nMODEL=local_fixture\nTOKEN="not_closed\nnever_expose_body'
    chunks, metadata = text_chunks(raw)
    body = "".join(text for _, text in chunks)
    assert "PORT=1234" in body and "MODEL=local_fixture" in body
    for secret in [
        "synthetic_unquoted_value",
        "one",
        "two",
        "synthetic_private_material",
        "not_closed",
        "never_expose_body",
    ]:
        assert secret not in body
    assert metadata["credential_assignments_redacted"] == 4
    assert metadata["unterminated_secret_values_masked_through_eof"] == 1


def test_unknown_binary_cannot_be_silently_decoded_as_replacement_characters():
    with pytest.raises(UnicodeDecodeError):
        decode_text(b"\xffnot_utf8")
    with pytest.raises(ValueError):
        decode_text(b"binary\x00control")
    value = "本地配置\nPORT=1234".encode("utf-16")
    assert decode_text(value)[0] == "本地配置\nPORT=1234"
    assert decode_text("本地配置".encode("utf-32"))[0] == "本地配置"


def test_extensionless_nested_text_search_download_and_resume_with_missing_originals(
    tmp_path,
):
    store = KnowledgeStore(tmp_path / "kb")
    raw = b"CONFIG_MARKER=local\nAPI_KEY=synthetic_secret_must_not_index\n"
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr(".env", raw)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("nested.zip", nested.getvalue())
    content = outer.getvalue()
    sha = hashlib.sha256(content).hexdigest()
    obj = store.root / "resources" / "objects" / sha
    obj.parent.mkdir(parents=True)
    obj.write_bytes(content)
    source = tmp_path / "source-gone"
    store.write_json(
        "resources-manifest.json",
        {
            "source": str(source),
            "completed": [
                {
                    "relative_path": "sample.zip",
                    "sha256": sha,
                    "bytes": len(content),
                    "object": str(obj.relative_to(store.root)),
                }
            ],
        },
    )
    store.write_json(
        "audit.json",
        {
            "completed": [
                {
                    "path": str(source / "sample.zip"),
                    "chain": ["nested.zip", ".env"],
                    "raw_digest": hashlib.sha256(raw).hexdigest(),
                }
            ]
        },
    )
    audit = store.root / "audit.json"
    report = index_text_resources(store, audit)
    assert len(report["completed"]) == 1 and not report["errors"]
    assert index_text_resources(store, audit)["completed"][0]["cached"]
    hits = store.search("CONFIG_MARKER")
    assert len(hits) == 1 and not store.search("synthetic_secret_must_not_index")
    with TestClient(create_app(store, "unused")) as client:
        document_id = -hits[0]["id"] - 1000000000
        assert client.get(f"/document-source/{document_id}").content == raw
