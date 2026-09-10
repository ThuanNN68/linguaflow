"""Tests for how the evaluation harness turns judge replies into numbers.

`eval/` is a script directory, not a package, so the module is loaded by path.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EVAL_PATH = Path(__file__).resolve().parents[2] / "eval" / "run_eval.py"


def load_run_eval():
    """Import eval/run_eval.py under its own name, once per session."""
    if "run_eval" in sys.modules:
        return sys.modules["run_eval"]

    spec = importlib.util.spec_from_file_location("run_eval", EVAL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_eval"] = module
    spec.loader.exec_module(module)
    return module


run_eval = load_run_eval()


def sample_result(**overrides) -> dict:
    """One entry as `run_sample` returns it, with every field populated.

    Written out in full rather than patched per test: `summarize` reads the
    whole record, and a test that omits half of it would pass against a
    summary that had quietly stopped reading those fields.
    """
    result = {
        "id": "gs-000",
        "category": "pronoun",
        "chat_type": "direct",
        "context_level": "rich",
        "language_pair": "vi->en",
        "source_language_declared": "vi",
        "source_language_detected": "vi",
        "original": "Deploy xong chưa anh?",
        "expected": "Is the deploy done?",
        "actual": "Is the deploy done?",
        "score": 1.0,
        "latency_ms": 500,
        "is_fallback": False,
        "outcome": "llm",
        "provider": "groq",
        "model_served": "llama-3.3-70b-versatile",
        "detect_method": "langdetect",
        "llm_calls": 1,
        "input_tokens": 100,
        "output_tokens": 10,
    }
    result.update(overrides)
    return result



@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.85", 0.85),
        ("1.0", 1.0),
        ("0", 0.0),
        ("  0.9\n", 0.9),
    ],
)
def test_a_bare_number_is_read_as_the_score(raw, expected):
    """The format the prompt asks for, with whitespace the model may add."""
    assert run_eval.parse_score(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "8/10",  # the loose pattern read this as 1.0
        "Score: 0.9",
        "0.9 — the pronoun choice is correct",
        "high",
        "",
    ],
)
def test_a_reply_that_is_not_a_bare_number_is_refused(raw):
    """A judge that ignored the output format also ignored its constraints.

    "8/10" is the case that matters. The previous pattern searched anywhere in
    the reply, found the "1" inside "10", and read a mediocre translation as a
    perfect 1.0.
    """
    assert run_eval.parse_score(raw) is None


def test_passthrough_samples_are_kept_out_of_the_score():
    """A sample returned verbatim scores ~1.0 without a model being called.

    Left in, it raises the average by rewarding work that never happened.
    """
    results = [
        sample_result(
            outcome="passthrough",
            score=1.0,
            language_pair="vi->vi",
            latency_ms=0,
            llm_calls=0,
        ),
        sample_result(outcome="llm", score=0.6, latency_ms=800),
    ]

    summary = run_eval.summarize(results)

    assert summary["total"] == 2
    assert summary["passthrough"] == 1
    assert summary["translated"] == 1
    assert summary["avg_score"] == 0.6
    assert "vi->vi" not in summary["by_language_pair"]


def test_categories_carry_their_sample_count():
    """Most category buckets hold one or two samples; a bare mean hides that."""
    results = [
        sample_result(category="idiom", score=score) for score in (1.0, 0.5)
    ]

    assert run_eval.summarize(results)["by_category"]["idiom"] == (2, 0.75, 1)


def test_an_unscored_sample_still_counts_towards_fallbacks():
    """The two figures have different denominators, and the report says so."""
    results = [sample_result(outcome="original", score=None, is_fallback=True)]

    summary = run_eval.summarize(results)

    assert summary["scored"] == 0
    assert summary["fallback"] == 1
    assert summary["avg_score"] == 0.0


def test_detect_agreement_measures_adr_11s_premise():
    """The two-tier detector only pays off if the local tier usually agrees."""
    results = [
        sample_result(source_language_declared="vi", source_language_detected="vi"),
        sample_result(source_language_declared="vi", source_language_detected="en"),
    ]

    assert run_eval.summarize(results)["detect_agreement"] == 0.5


def make_run(avg_score: float, passed: int, sample_scores: dict[str, float]) -> dict:
    """A stored run, as `write_run` would have persisted it."""
    return {
        "run_id": "20260101-120000-abcdef",
        "provider": "groq",
        "golden_set_sha": "deadbeef1234",
        "summary": {
            "avg_score": avg_score,
            "passed": passed,
            "scored": len(sample_scores),
            "avg_latency": 800.0,
            "p95_latency": 1500.0,
            "fallback": 0,
        },
        "samples": [
            sample_result(id=sample_id, score=score)
            for sample_id, score in sample_scores.items()
        ],
    }


def test_a_small_movement_is_reported_as_noise():
    """Two runs of identical code differ; saying so is more useful than a delta.

    Both models run at temperature 0.3 and each sample is scored once, so a
    third of a point on one sample moves the average without meaning anything.
    """
    baseline = make_run(0.90, 2, {"gs-001": 1.0, "gs-002": 0.8})
    summary = {
        "avg_score": 0.92,
        "passed": 2,
        "scored": 2,
        "avg_latency": 810.0,
        "p95_latency": 1520.0,
        "fallback": 0,
    }
    current = {
        "provider": "groq",
        "golden_set_sha": "deadbeef1234",
        "samples": [
            sample_result(id="gs-001", score=1.0),
            sample_result(id="gs-002", score=0.84),
        ],
    }

    output = run_eval.compare_runs(baseline, current, summary)

    assert "trong khoảng nhiễu" in output
    assert "Không mẫu nào đổi trạng thái" in output


def test_a_sample_crossing_the_threshold_is_named():
    """An average that barely moves can still hide a sample that broke."""
    baseline = make_run(0.85, 2, {"gs-001": 1.0, "gs-002": 0.75})
    summary = {
        "avg_score": 0.80,
        "passed": 1,
        "scored": 2,
        "avg_latency": 800.0,
        "p95_latency": 1500.0,
        "fallback": 0,
    }
    current = {
        "provider": "groq",
        "golden_set_sha": "deadbeef1234",
        "samples": [
            sample_result(id="gs-001", score=1.0),
            sample_result(id="gs-002", score=0.60),
        ],
    }

    output = run_eval.compare_runs(baseline, current, summary)

    assert "gs-002: đạt → chưa đạt" in output


def test_an_edited_dataset_is_flagged():
    """Comparing across two different golden sets compares nothing."""
    baseline = make_run(0.90, 1, {"gs-001": 0.9})
    summary = {
        "avg_score": 0.90,
        "passed": 1,
        "scored": 1,
        "avg_latency": 800.0,
        "p95_latency": 1500.0,
        "fallback": 0,
    }
    current = {
        "provider": "groq",
        "golden_set_sha": "0000ffff9999",
        "samples": [sample_result(id="gs-001", score=0.9)],
    }

    assert "Bộ dữ liệu đã thay đổi" in run_eval.compare_runs(baseline, current, summary)
