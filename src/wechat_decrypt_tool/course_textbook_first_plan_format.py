"""Actual-count planning grammar, installed before an original role is used."""

from __future__ import annotations

from pathlib import Path
import re

from . import course_textbook_one_based_writer as counted

fresh, writing, digest = counted.fresh, counted.writing, counted.digest
POLICY = "first-use-actual-group-count-planning-format-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_first_plan_format_" + code)


def binding():
    return {
        "policy": POLICY,
        "module_sha256": writing.whole.sha(Path(__file__).read_bytes()),
        "counted_module_sha256": writing.whole.sha(Path(counted.__file__).read_bytes()),
    }


def job_type(base, execution, *, read_only):
    class CountedPlanner(base):
        def stage(self, name, prompt, schema, context, validate, **kwargs):
            if not re.fullmatch(counted.PLAN, name):
                return super().stage(name, prompt, schema, context, validate, **kwargs)
            require(
                prompt == writing.author.GROUP_PROMPT
                and set(context) == {"items", "mode"}
                and context["mode"] in {"concepts", "chapters"}
                and not kwargs
                and self.max_attempts == 2,
                "unchanged_original_planning_role_and_budget",
            )
            cards, final = context["items"], context["mode"] == "chapters"
            require(
                schema == writing.author.grouping_schema(cards, final=final),
                "complete_original_planning_schema",
            )
            actual_schema = counted.counted_schema(cards, final=final)
            actual_context = {
                **context,
                "group_numbering": "Declare actual group_count, exactly that many groups; assignment indices start at zero.",
            }
            path = self.root / (name + "-first-counted-plan-request.json")
            request = {
                "format": binding(),
                "execution_sha256": digest(execution),
                "stage": name,
                "original_schema_sha256": digest(schema),
                "actual_schema_sha256": digest(actual_schema),
                "original_context_sha256": digest(context),
                "actual_context_sha256": digest(actual_context),
                "attempt_budget": self.max_attempts,
                "original_budget_reset": False,
            }
            if not path.exists():
                require(
                    not read_only
                    and not (self.root / (name + ".json")).exists()
                    and not list((self.root / "attempts").glob(name + "-*.json")),
                    "format_before_first_original_attempt",
                )
            fresh.retain(path, request, read_only=read_only)

            def check(raw):
                validate(counted.compile_counted(raw, cards, final=final))

            if not read_only and not (self.root / (name + ".json")).exists():
                fresh.BASE_JOB.stage(
                    self, name, prompt, actual_schema, actual_context, check
                )
            raw = self.reader.stage(name, prompt, actual_schema, actual_context, check)
            value = counted.compile_counted(raw, cards, final=final)
            derivation = self.root / (name + "-first-counted-plan-derived.json")
            fresh.retain(
                derivation,
                {
                    "format": binding(),
                    "execution_sha256": digest(execution),
                    "actual_response_sha256": digest(raw),
                    "candidate": value,
                    "candidate_sha256": digest(value),
                    "semantic_group_memberships_changed": False,
                    "semantic_acceptance": False,
                },
                read_only=read_only,
            )
            self.reader.paths.update([path, derivation])
            return value

    return CountedPlanner
