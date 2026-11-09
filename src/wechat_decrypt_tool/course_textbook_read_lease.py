"""Bounded waiting for immutable ledger reads, without changing model budgets."""
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def bounded_ledger_reads(writing, *, seconds=300):
    if type(seconds) is not int or not 1 <= seconds <= 300:
        raise ValueError("requires_ledger_read_wait_finite_timeout")
    original = writing.schema_lease

    def lease(path, timeout=300):
        # Keep every mutex and all other fail-fast worker locks. This only queues
        # readers of the original immutable ledger; it never retries inference.
        actual = seconds if Path(path).name == "ledger-worker.lock" and timeout == 0 else timeout
        return original(path, timeout=actual)

    writing.schema_lease = lease
    try:
        yield
    finally:
        writing.schema_lease = original
