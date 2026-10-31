import hashlib
import io
import json
import zipfile

from fastapi.testclient import TestClient

from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app
from wechat_decrypt_tool.course_tabular_resources import (
    index_spreadsheets,
    workbook_chunks,
)


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return buffer.getvalue()


def workbook():
    return zipped(
        {
            "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
            "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Fixture" sheetId="1" r:id="rId1"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:D3"/><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>nested_table_marker</t></is></c><c r="B1" t="n"><v>0</v></c><c r="C1" t="b"><v>0</v></c><c r="D1"><f>SUM(B1:B2)</f><v>3</v></c></row><row r="2"><c r="B2" t="n"><v>3</v></c><c r="D2"><f>1+1</f><v></v></c></row><row r="3"><c r="A3" t="d"><v>2024-01-02T00:00:00</v></c></row></sheetData></worksheet>',
        }
    )


def test_read_only_cells_preserve_zero_false_dates_and_unavailable_formula_cache():
    raw = workbook()
    chunks, metadata = workbook_chunks(raw)
    cells = {
        value["cell"]: value
        for _, body in chunks
        for value in map(json.loads, body.splitlines())
    }
    assert metadata["nonempty_cells"] == 7
    assert (
        metadata["formula_cells"] == 2
        and metadata["formula_cached_values_missing"] == 1
    )
    assert cells["B1"]["value"] == 0 and type(cells["B1"]["value"]) is int
    assert cells["C1"]["value"] is False
    assert cells["A3"]["value"] == "2024-01-02T00:00:00"
    assert cells["D1"]["cached_value"] == 3
    assert cells["D2"]["value"] == "=1+1" and cells["D2"]["cached_value"] is None
    assert not metadata["recalculated"]


def corpus(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge")
    raw = workbook()
    outer = zipped({"inner.zip": zipped({"sample.xlsx": raw})})
    sha = hashlib.sha256(outer).hexdigest()
    obj = store.root / "resources" / "objects" / sha
    obj.parent.mkdir(parents=True)
    obj.write_bytes(outer)
    source = tmp_path / "originals-do-not-exist"
    store.write_json(
        "resources-manifest.json",
        {
            "source": str(source),
            "completed": [
                {
                    "relative_path": name,
                    "sha256": sha,
                    "bytes": len(outer),
                    "object": str(obj.relative_to(store.root)),
                }
                for name in ("a.zip", "b.zip")
            ],
        },
    )
    audit = store.root / "audit.json"
    store.write_json(
        audit.name,
        {
            "completed": [
                {
                    "path": str(source / "a.zip"),
                    "chain": ["inner.zip", "sample.xlsx"],
                    "raw_digest": hashlib.sha256(raw).hexdigest(),
                }
            ]
        },
    )
    return store, audit, obj, raw


def test_all_identical_container_aliases_index_once_per_source_and_download_exact_xlsx(
    tmp_path,
):
    store, audit, _, raw = corpus(tmp_path)
    report = index_spreadsheets(store, audit)
    assert len(report["completed"]) == 2 and not report["errors"]
    second = index_spreadsheets(store, audit)
    assert all(row["cached"] for row in second["completed"])
    hits = store.search("nested_table_marker")
    assert len(hits) == 2
    with TestClient(create_app(store, "unused")) as client:
        for hit in hits:
            document_id = -hit["id"] - 1000000000
            assert client.get(f"/document-source/{document_id}").content == raw


def test_corrupt_retained_container_never_indexes_spreadsheet_cells(tmp_path):
    store, audit, obj, _ = corpus(tmp_path)
    value = bytearray(obj.read_bytes())
    value[-1] ^= 1
    obj.write_bytes(value)
    report = index_spreadsheets(store, audit)
    assert not report["completed"] and len(report["errors"]) == 2
    assert not store.search("nested_table_marker")
