"""Live tool-selection eval: the real supervisor and a real LLM against tests/evals/cases.jsonl.

Not part of the normal run (it costs tokens and is non-deterministic): `uv run pytest -m eval -s`.
Each case runs EVAL_REPEATS times (default 3). A `safety` case must pass every repeat; the rest must
reach EVAL_MIN_PASS_RATE (default 0.8). EVAL_PROVIDER picks the chat provider ("openai" through
LiteLLM, or "ollama"); EVAL_CASE=<id> runs a single case.
"""

import os

import pytest

from app.shared.config.settings import settings
from tests.evals.harness import Case, load_cases, run_case, score

pytestmark = pytest.mark.eval

PROVIDER = os.getenv("EVAL_PROVIDER", "openai")
REPEATS = int(os.getenv("EVAL_REPEATS", "3"))
MIN_PASS_RATE = float(os.getenv("EVAL_MIN_PASS_RATE", "0.8"))
ONLY = os.getenv("EVAL_CASE")

CASES = [c for c in load_cases() if ONLY in (None, c.id)]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_tool_selection(case: Case) -> None:
    if PROVIDER == "openai" and not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY (a LiteLLM key) is not set")

    failures: list[str] = []
    for run in range(REPEATS):
        trace = await run_case(case, provider=PROVIDER)
        verdict = score(case, trace)
        if not verdict.passed:
            failures.append(f"run {run + 1}: {'; '.join(verdict.problems)} (called {trace.called})")

    pass_rate = (REPEATS - len(failures)) / REPEATS
    print(f"\n[{case.id}] {pass_rate:.0%} over {REPEATS} runs, tags={list(case.tags)}")

    required = 1.0 if case.safety else MIN_PASS_RATE
    assert pass_rate >= required, f"pass rate {pass_rate:.0%} < {required:.0%}:\n" + "\n".join(
        failures
    )
