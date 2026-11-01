"""Run a restored reader with original data and source-code reads forbidden."""

import json
import os
import sys
from pathlib import Path


root = Path(sys.argv[1]).resolve()
forbidden = [Path(value).resolve() for value in sys.argv[2:]]
bundle = root / "runtime/src"
sys.path.insert(0, str(bundle))
blocked = 0


def guard(event, args):
    global blocked
    if event == "sqlite3.connect":
        raise PermissionError("restored_reader_must_not_open_sqlite")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if any(path.is_relative_to(folder) for folder in forbidden):
            blocked += 1
            raise PermissionError("original_archive_and_source_reads_forbidden")


sys.addaudithook(guard)
for folder in forbidden:
    try:
        (folder / "forbidden-probe").read_bytes()
    except PermissionError:
        pass
    else:
        raise RuntimeError("isolation_guard_not_active")
baseline = blocked
from wechat_decrypt_tool.course_wiki_snapshot import smoke  # noqa: E402

result = smoke(root)
modules = [
    module
    for name, module in sys.modules.items()
    if name == "wechat_decrypt_tool" or name.startswith("wechat_decrypt_tool.")
]
if not all(
    Path(module.__file__).resolve().is_relative_to(bundle) for module in modules
):
    raise RuntimeError("reader_imported_original_package")
if blocked != baseline:
    raise RuntimeError("reader_attempted_original_archive_access")
result.update(
    isolated_reader_verified=True,
    bundled_modules_verified=len(modules),
    original_root_guard_probes=baseline,
    original_root_access_attempts=blocked - baseline,
)
print(json.dumps(result))
