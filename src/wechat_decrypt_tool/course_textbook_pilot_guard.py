"""Launch an unchanged, prepared finite ledger trial with a supported GPU role."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import course_textbook_ledger_pilot as pilot
from .course_archive_gpu import checked_sample
from .course_gpu_guard import run as guarded


def run(root, inventory_key, tokenizer):
    root = Path(root).resolve()
    pilot.whole.TextbookStore(root).checkpoint()
    inventory = pilot.whole.load(root, inventory_key)
    binding = pilot.make_binding(inventory, tokenizer)
    key = pilot.whole.sha(pilot.canonical(binding))[:32]
    pilot.require(pilot.whole.read(root / "wiki/textbook-keyed-jobs" / key / "manifest.json")
        == {"id": key, "binding": binding}, "unchanged_prepared_execution")
    previous = root / "review-experiment-thermal-guard.json"
    if previous.exists():
        pilot.require(not pilot.whole_identity_live(pilot.whole.read(previous)), "prior_experiment_guard_exited")
        pilot.whole.backup(previous, root / "wiki/pilot-supported-guard-history" / (pilot.whole.sha(previous.read_bytes()) + ".json"))
    guarded(root, [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_ledger_pilot", "--root", str(root),
        "--inventory", inventory_key, "--tokenizer", str(Path(tokenizer).resolve()), "worker"],
        maximum_temperature=70, maximum_power=200, guard_name="review-experiment-thermal-guard", sampler=lambda: checked_sample(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.root, args.inventory, args.tokenizer)
    except Exception as exc:
        print(json.dumps(pilot.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
