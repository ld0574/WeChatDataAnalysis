from wechat_decrypt_tool import course_archive_gpu
from wechat_decrypt_tool import course_gpu_guard
from wechat_decrypt_tool import course_textbook_manuscript_entry as entry


def parameters(root):
    return [
        "--root",
        str(root),
        "--inventory",
        "a" * 64,
        "--ledger-job",
        "b" * 32,
        "--job",
        "c" * 32,
        "--legacy-section",
        "section-0001",
    ]


def test_worker_imports_the_bound_controller_instead_of_running_it_as_main(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(entry.controller, "main", lambda argv: calls.append(argv))
    entry.main([*parameters(tmp_path), "run"])
    assert calls == [[*parameters(tmp_path.resolve()), "run"]]
    assert (
        entry.controller.__name__
        == "wechat_decrypt_tool.course_textbook_manuscript_continue"
    )


def test_guard_relaunches_the_import_stable_entry_with_unchanged_limits(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(
        course_archive_gpu, "checked_sample", lambda root: {"verified": True}
    )

    def guard(root, command, **kwargs):
        calls.append((root, command, kwargs))

    monkeypatch.setattr(course_gpu_guard, "run", guard)
    entry.main([*parameters(tmp_path), "guard"])
    root, command, kwargs = calls[0]
    assert root == tmp_path
    assert command[1:] == [
        "-m",
        "wechat_decrypt_tool.course_textbook_manuscript_entry",
        *parameters(tmp_path.resolve()),
        "run",
    ]
    assert kwargs["maximum_temperature"] == 70
    assert kwargs["maximum_power"] == 200
    assert kwargs["guard_name"] == "wiki-revision-thermal-guard"
    assert kwargs["sampler"]() == {"verified": True}
