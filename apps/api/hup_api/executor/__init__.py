"""Executor (B2): validate_plan(plan, catalog) -> list[str] and run(plan) -> analysis_id."""
from hup_api.executor import runner
from hup_api.executor.runner import InvalidPlan, run
from hup_api.executor.validation import validate_plan

__all__ = ["InvalidPlan", "run", "runner", "validate_plan"]
