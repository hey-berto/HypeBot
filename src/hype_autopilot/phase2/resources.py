from __future__ import annotations

import os
import resource
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hype_autopilot.phase2.config import ResourceIsolation


class ResourceBudgetExceeded(RuntimeError):
    pass


class Phase2ResourceGuard:
    """Conservative process, concurrency, disk, and API-cost isolation for Phase 2."""

    def __init__(self, policy: ResourceIsolation, database_path: str | Path) -> None:
        self.policy = policy
        self.database_path = Path(database_path)
        self._semaphore = threading.BoundedSemaphore(policy.max_concurrent_llm_calls)

    def apply_process_limits(self) -> None:
        memory_bytes = self.policy.max_process_memory_mb * 1024 * 1024
        _, current_hard = resource.getrlimit(resource.RLIMIT_AS)
        hard = (
            memory_bytes
            if current_hard == resource.RLIM_INFINITY
            else min(current_hard, memory_bytes)
        )
        resource.setrlimit(resource.RLIMIT_AS, (min(memory_bytes, hard), hard))
        if self.policy.process_nice_increment:
            os.nice(self.policy.process_nice_increment)

    def assert_disk_budget(self) -> None:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.database_path}{suffix}")
            if candidate.exists():
                total += candidate.stat().st_size
        if total > self.policy.max_database_size_mb * 1024 * 1024:
            raise ResourceBudgetExceeded("Phase 2 database disk budget exceeded")

    def assert_api_budget(self, spent_today_usd: float) -> None:
        if spent_today_usd >= self.policy.api_budget_usd_per_day:
            raise ResourceBudgetExceeded("Phase 2 daily API budget exhausted")

    @contextmanager
    def llm_slot(self) -> Iterator[None]:
        if not self._semaphore.acquire(blocking=False):
            raise ResourceBudgetExceeded("Phase 2 concurrent LLM-call limit reached")
        try:
            yield
        finally:
            self._semaphore.release()
