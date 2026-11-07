"""Launch an unchanged, prepared finite ledger trial with a supported GPU role."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import course_textbook_ledger_pilot as pilot
from .course_archive_gpu import checked_sample
from .course_gpu_guard import run as guarded
from .course_knowledge import schema_lease


def trial_ready(root, key, inventory):
    folder = root / "wiki/textbook-keyed-jobs" / key
    trial = pilot.whole.read(folder / "trial-result.json")
    pilot.require(trial["job"] == key and trial["inventory"] == pilot.whole.sha(pilot.canonical(inventory))
        and trial["state"] == "first_role_batch_completed_quality_and_throughput_review_pending"
        and trial["completed_groups"] == min(pilot.BATCH_GROUPS, len(inventory["pages"]))
        and trial["unresolved_groups"] == 0 and not trial["reader_article_published"], "successful_original_trial_required")
    pilot.require(not (folder / "ledger.json").exists(), "completed_ledger_must_not_be_restarted")
    for number in range(trial["completed_groups"]):
        saved = pilot.whole.read(folder / f"group-{number:04d}.json")
        context = pilot.whole.context_for(inventory, inventory["pages"][number])
        selected = {ref: inventory["sources"][ref] for ref in inventory["pages"][number]}
        pilot.require(saved["input_sha256"] == pilot.whole.sha(pilot.canonical(context))
            and saved["data_sha256"] == pilot.whole.sha(pilot.canonical(saved["data"]))
            and bool(saved["stage_files"])
            and all(Path(path).is_file() and pilot.whole.sha(Path(path).read_bytes()) == digest
                for path, digest in saved["stage_files"].items()), "unchanged_successful_trial_group")
        pilot.whole.validate_ledger(saved["data"]["candidate"], selected)
        pilot.whole.keyed.validate_audit(saved["data"]["audit"], saved["data"]["candidate"], selected)
        pilot.require(pilot.whole.audit_passed(saved["data"]["audit"]), "trial_group_independently_passed")


def run(root, inventory_key, tokenizer, *, complete=False):
    root = Path(root).resolve()
    pilot.whole.TextbookStore(root).checkpoint()
    inventory = pilot.whole.load(root, inventory_key)
    binding = pilot.make_binding(inventory, tokenizer)
    key = pilot.whole.sha(pilot.canonical(binding))[:32]
    pilot.require(pilot.whole.read(root / "wiki/textbook-keyed-jobs" / key / "manifest.json")
        == {"id": key, "binding": binding}, "unchanged_prepared_execution")
    with schema_lease(root / "wiki/whole-source-pilot-dispatch.lock", timeout=0):
        previous = root / "review-experiment-thermal-guard.json"
        if previous.exists():
            old = pilot.whole.read(previous)
            pilot.require(not pilot.whole_identity_live(old), "prior_experiment_guard_exited")
            if complete:
                pilot.require(old["state"] == "worker_exited" and old.get("returncode") == 0, "trial_guard_success_required")
            pilot.whole.backup(previous, root / "wiki/pilot-supported-guard-history" / (pilot.whole.sha(previous.read_bytes()) + ".json"))
        if complete:
            trial_ready(root, key, inventory)
        command = [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_ledger_pilot", "--root", str(root),
            "--inventory", inventory_key, "--tokenizer", str(Path(tokenizer).resolve())]
        if complete:
            command.append("--complete")
        guarded(root, [*command, "worker"], maximum_temperature=70, maximum_power=200,
            guard_name="review-experiment-thermal-guard", sampler=lambda: checked_sample(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    try:
        run(args.root, args.inventory, args.tokenizer, complete=args.complete)
    except Exception as exc:
        print(json.dumps(pilot.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
