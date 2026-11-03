"""Import-stable CLI for an already bound private manuscript controller."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import course_textbook_manuscript_continue as controller


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("inventory", "ledger-job", "job", "legacy-section"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("command", choices=["run", "guard"])
    args = parser.parse_args(argv)
    parameters = ["--root", str(args.root.resolve())]
    for flag in ("inventory", "ledger-job", "job", "legacy-section"):
        parameters += ["--" + flag, getattr(args, flag.replace("-", "_"))]
    if args.command == "run":
        # Keep the controller's module identity equal to its preflight import.
        # Running it as __main__ changes its execution-contract module key.
        return controller.main([*parameters, "run"])

    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    return guarded(
        args.root,
        [sys.executable, "-m", __spec__.name, *parameters, "run"],
        maximum_power=200,
        maximum_temperature=70,
        guard_name="wiki-revision-thermal-guard",
        sampler=lambda: checked_sample(args.root),
    )


if __name__ == "__main__":
    main()
