"""Isolated Phase 2 LLM research infrastructure.

This package is build-only until a separate explicit evidence-collection authorization.
"""

from hype_autopilot.phase2.hybrid import HybridAgreementStrategy
from hype_autopilot.phase2.pipeline import Phase2Pipeline
from hype_autopilot.phase2.runner import FailClosedLLMRunner
from hype_autopilot.phase2.runtime import Phase2Runtime, build_phase2_runtime

__all__ = [
    "FailClosedLLMRunner",
    "HybridAgreementStrategy",
    "Phase2Pipeline",
    "Phase2Runtime",
    "build_phase2_runtime",
]
