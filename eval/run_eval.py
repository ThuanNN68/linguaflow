"""Translation quality scoring over the golden set (F-03.3).

Runs the Translation Agent across eval/golden_set.jsonl, scores each sample with
an LLM-as-judge and writes the results to eval/results/report.md.

Usage:
    python eval/run_eval.py                 # run every sample
    python eval/run_eval.py --limit 5       # smoke test on the first 5
    python eval/run_eval.py --no-write      # print only, do not write the report

Requires an LLM provider configured in .env (see LLM_PROVIDER). Scores come from
a model, not from human raters, so they are only comparable between runs.

Each golden-set row carries two classification fields the report breaks results
down by:

* ``chat_type`` — ``direct`` for a bilingual one-to-one thread, ``group`` for a
  thread whose context mixes three or more languages.
* ``context_level`` — ``rich`` when two or more context messages are supplied
  (three or more for group threads), ``poor`` for zero or one. This is the
  supplied-context count, not a judgement about how much context the sentence
  needs.

Speakers in ``context_messages`` are coded ``U01``, ``U02``, ``U03``, numbered in
order of first appearance within each conversation. They carry no name and no
role, because the real ``ContextProvider`` supplies none — ``context_messages``
is a plain ``list[str]`` per docs/api/contract.md section 2. Choosing the right
register and pronouns from the conversation alone is the behaviour under test,
so labelling a speaker as a client or a developer would hand the model the
answer. Scenario intent is documented in each row's ``note`` field, which is
never sent to the model.

The generated report is written in Vietnamese: it is a team deliverable, and
project documentation is Vietnamese by convention.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import secrets
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

# Allows running the file directly: python eval/run_eval.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402

from src.agents.context_provider import InMemoryContextProvider  # noqa: E402
from src.agents.graph import build_translation_graph  # noqa: E402
from src.agents.observability import build_runnable_config  # noqa: E402
from src.agents.prompts import build_context_block  # noqa: E402
from src.config import configure_logging, get_settings  # noqa: E402
from src.services.shared.llm import extract_text, get_llm  # noqa: E402
from src.services.shared.metrics import group_scores, percentile  # noqa: E402

GOLDEN_SET = Path(__file__).parent / "golden_set.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"
REPORT_PATH = RESULTS_DIR / "report.md"

# A judge score at or above this counts the translation as acceptable
PASS_THRESHOLD = 0.7

# Report targets. TARGET_LATENCY_MS follows NFR-01 (ARCHITECTURE.md section 5);
# the p95 threshold is an internal convention, not part of the requirements.
TARGET_PASS_RATE = 80.0
TARGET_AVG_SCORE = 0.80
TARGET_LATENCY_MS = 1000
TARGET_P95_LATENCY_MS = 2000

JUDGE_PROMPT = """\
# Role
You are a translation quality judge.

# Task
Score the system translation against the reference translation.

Source language: {source_language}
Target language: {target_language}
{context_block}Original: {original_text}
Reference translation: {expected}
System translation: {actual}

# Scoring criteria
- Meaning preserved relative to the original (most important)
- Correct pronouns and recovered subjects given the conversation history above
- Technical terms, proper nouns and figures kept intact
- Reads naturally in the target language

# Constraints
- The system translation need not match the reference word for word. Different \
wording that carries the same meaning still scores high.
- Return one decimal number between 0.0 and 1.0. No explanation, no other \
characters.\
"""

# Anchored at both ends. A loose search reads "8/10" as 1.0 and hands a mediocre
# translation a perfect score, which is worse than recording no score at all. The
# prompt asks for a bare number, so anything else is a judge that did not comply
# — a signal in its own right, not something to salvage by guessing.
_SCORE_PATTERN = re.compile(r"\s*([01](?:\.\d+)?)\s*")


def load_golden_set(limit: int | None = None) -> list[dict]:
    """Load translation test samples from golden_set.jsonl.

    Args:
        limit: Optional maximum number of samples to load.

    Returns:
        List of sample dictionaries.
    """
    if not GOLDEN_SET.exists():
        raise FileNotFoundError(f"Không tìm thấy golden set: {GOLDEN_SET}")

    samples = []
    with GOLDEN_SET.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Dòng {line_no} không phải JSON hợp lệ: {exc}") from exc

    return samples[:limit] if limit else samples



async def judge(sample: dict, actual: str, judge_llm: BaseChatModel) -> float | None:
    """Score one translation. Returns None when the judge could not score it."""
    prompt = JUDGE_PROMPT.format(
        source_language=sample["source_language"],
        target_language=sample["target_language"],
        # The judge is asked to grade recovered subjects and pronoun choice
        # "given the conversation history", and until now was never shown any.
        # Rendered through the same helper the agent uses, so the judge sees
        # exactly the context the agent saw, sanitised the same way.
        context_block=build_context_block(sample.get("context_messages", [])),
        original_text=sample["original_text"],
        expected=sample["expected_translation"],
        actual=actual,
    )
    try:
        response = await judge_llm.ainvoke(prompt)
        raw = extract_text(response)
    except Exception as exc:
        print(f"    [!] Judge lỗi: {type(exc).__name__}: {exc}")
        return None

    return parse_score(raw)


def parse_score(raw: str) -> float | None:
    """Read the judge's reply as a score, or refuse it.

    Returns:
        The score, or None when the reply was not a bare number — a judge that
        ignored the output format has also had its reasoning unconstrained, so
        its answer is not worth recovering.
    """
    match = _SCORE_PATTERN.fullmatch(raw)
    if not match:
        print(f"    [!] Judge trả về không phải điểm số: {raw[:60]!r}")
        return None

    return min(1.0, max(0.0, float(match.group(1))))


async def run_sample(
    sample: dict,
    graph,
    provider: InMemoryContextProvider,
    judge_llm: BaseChatModel,
) -> dict:
    """Run the agent on one sample and score the result."""
    conversation_id = f"eval-{sample['id']}"

    for msg in sample.get("context_messages", []):
        provider.add_message(conversation_id, msg)

    state = await graph.ainvoke(
        {
            "conversation_id": conversation_id,
            "original_text": sample["original_text"],
            "source_language": sample["source_language"],
            "target_language": sample["target_language"],
        },
        config=build_runnable_config(
            conversation_id=conversation_id,
            sample_id=sample["id"],
            category=sample.get("category"),
        ),
    )

    actual = state.get("translated_text", "")
    score = await judge(sample, actual, judge_llm)
    telemetry = state.get("telemetry", {})
    declared = sample["source_language"]
    detected = state.get("source_language") or declared

    return {
        "id": sample["id"],
        "category": sample.get("category", ""),
        "chat_type": sample.get("chat_type", ""),
        "context_level": sample.get("context_level", ""),
        # Built from the language actually detected, not the one the sample
        # declares. Under the declared value a misdetected sample is filed
        # under a pair it was never translated as, and the error disappears
        # into a bucket that looks healthy.
        "language_pair": f"{detected}->{sample['target_language']}",
        "source_language_declared": declared,
        "source_language_detected": detected,
        "original": sample["original_text"],
        "expected": sample["expected_translation"],
        "actual": actual,
        "score": score,
        "latency_ms": state.get("latency_ms", 0),
        "is_fallback": state.get("is_fallback", False),
        "outcome": telemetry.get("outcome", ""),
        # Attribution: which provider and model produced this particular
        # translation, and what it cost. Without it a report is a set of
        # numbers with nothing to attribute them to.
        "provider": get_settings().llm_provider,
        "model_served": telemetry.get("model_served", ""),
        "detect_method": telemetry.get("detect_method", ""),
        "llm_calls": telemetry.get("llm_calls", 0),
        "input_tokens": telemetry.get("input_tokens", 0),
        "output_tokens": telemetry.get("output_tokens", 0),
    }


def summarize(results: list[dict]) -> dict:
    """Aggregate evaluation scores and latency metrics across test results.

    Args:
        results: List of scored result dictionaries.

    Returns:
        Summary statistics dictionary.
    """
    # A passthrough sample (source language already equals target) is returned
    # verbatim without a model being called, and the judge duly scores it ~1.0.
    # Counting that as translation quality inflates every aggregate it touches
    # with work nobody did, so it is reported separately instead. It was already
    # missing from the latency figures by accident — the `> 0` filter below —
    # which is now the deliberate treatment across the board.
    passthrough = [r for r in results if r["outcome"] == "passthrough"]
    translated = [r for r in results if r["outcome"] != "passthrough"]

    scored = [r for r in translated if r["score"] is not None]
    latencies = [r["latency_ms"] for r in translated if r["latency_ms"] > 0]

    return {
        "by_chat_type": group_scores(scored, "chat_type", PASS_THRESHOLD),
        "by_context_level": group_scores(scored, "context_level", PASS_THRESHOLD),
        "by_language_pair": group_scores(scored, "language_pair", PASS_THRESHOLD),
        "by_category": group_scores(scored, "category", PASS_THRESHOLD),
        "total": len(results),
        "translated": len(translated),
        "passthrough": len(passthrough),
        "scored": len(scored),
        "passed": sum(1 for r in scored if r["score"] >= PASS_THRESHOLD),
        "fallback": sum(1 for r in translated if r["is_fallback"]),
        "avg_score": statistics.mean([r["score"] for r in scored]) if scored else 0.0,
        "avg_latency": statistics.mean(latencies) if latencies else 0.0,
        "p95_latency": percentile(latencies, 95),
        # Tests ADR-11's premise directly. The two-tier design assumes people
        # usually write in the language they configured, so the local detector
        # usually agrees and the arbitration call is rarely needed. If this rate
        # were low, the design would be paying for a step it does not save.
        "detect_agreement": (
            sum(
                1
                for r in results
                if r["source_language_detected"] == r["source_language_declared"]
            )
            / len(results)
            if results
            else 0.0
        ),
        "llm_calls": sum(r["llm_calls"] for r in results),
        "input_tokens": sum(r["input_tokens"] for r in results),
        "output_tokens": sum(r["output_tokens"] for r in results),
    }


def _metric_rows(summary: dict, accuracy: float) -> list[tuple[str, str, str, bool]]:
    """Metric table rows: label, target, actual value, and whether it passed.

    Each threshold appears once per row so that editing a label cannot leave the
    comparison behind it out of sync.
    """
    return [
        (
            f"Tỷ lệ đạt (điểm ≥ {PASS_THRESHOLD})",
            f"> {TARGET_PASS_RATE:.0f}%",
            f"{accuracy:.1f}%",
            accuracy > TARGET_PASS_RATE,
        ),
        (
            "Điểm trung bình",
            f"> {TARGET_AVG_SCORE:.2f}",
            f"{summary['avg_score']:.3f}",
            summary["avg_score"] > TARGET_AVG_SCORE,
        ),
        (
            "Độ trễ trung bình",
            f"< {TARGET_LATENCY_MS}ms",
            f"{summary['avg_latency']:.0f}ms",
            summary["avg_latency"] < TARGET_LATENCY_MS,
        ),
        (
            "Độ trễ p95",
            f"< {TARGET_P95_LATENCY_MS}ms",
            f"{summary['p95_latency']:.0f}ms",
            summary["p95_latency"] < TARGET_P95_LATENCY_MS,
        ),
        (
            "Số lần fallback",
            "0",
            str(summary["fallback"]),
            summary["fallback"] == 0,
        ),
    ]


def render_report(
    results: list[dict], summary: dict, judge_provider: str | None = None
) -> str:
    """Generate Markdown report for evaluation results.

    Args:
        results: List of scored sample results.
        summary: Aggregated summary statistics.
        judge_provider: Name of the LLM provider used as judge.

    Returns:
        Formatted markdown report string.
    """
    settings = get_settings()
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    accuracy = summary["passed"] / summary["scored"] * 100 if summary["scored"] else 0
    judge_name = judge_provider or settings.llm_provider
    is_self_judging = judge_name == settings.llm_provider

    lines = [
        "# BÁO CÁO ĐÁNH GIÁ CHẤT LƯỢNG DỊCH",
        "",
        "**Dự án:** LinguaFlow",
        f"**Thời điểm chạy:** {now}",
        f"**Provider dịch:** {settings.llm_provider} · "
        f"**Model:** {settings.llm_model or 'mặc định của provider'}",
        f"**Provider chấm điểm:** {judge_name}",
        f"**Bộ dữ liệu:** `eval/golden_set.jsonl` ({summary['total']} mẫu)",
        "",
        "> Điểm số do LLM tự chấm (LLM-as-judge), không phải đánh giá của người thật. "
        "Kết quả dùng để so sánh tương đối giữa các lần chạy và giữa các provider.",
        "",
    ]

    # Keyed on the providers actually being equal, not on whether the flag was
    # passed: --judge-provider groq with LLM_PROVIDER=groq is still self-judging.
    if is_self_judging:
        lines += [
            "> **CẢNH BÁO:** provider chấm điểm trùng provider dịch — model đang tự "
            "chấm chính nó nên điểm số bị thiên vị. Dùng `--judge-provider` để chỉ "
            "định một model trung lập.",
            "",
        ]

    lines += [
        "## 1. Chỉ số tổng hợp",
        "",
        "| Chỉ số | Mục tiêu | Thực tế | Trạng thái |",
        "|---|---|---|---|",
    ]

    for label, target, actual, is_passed in _metric_rows(summary, accuracy):
        lines.append(f"| {label} | {target} | {actual} | {'Đạt' if is_passed else 'Chưa đạt'} |")

    lines += [
        "",
        f"Mẫu đo: {summary['translated']}/{summary['total']} — "
        f"{summary['passthrough']} mẫu passthrough (nguồn trùng đích) bị loại khỏi mọi "
        "chỉ số vì không có model nào được gọi, judge vẫn chấm ~1.0 và điều đó chỉ làm "
        "đẹp số liệu.",
        "",
        f"Hai chỉ số dùng hai mẫu số khác nhau: **tỷ lệ đạt** tính trên "
        f"{summary['scored']} mẫu judge chấm được, còn **số lần fallback** tính trên cả "
        f"{summary['translated']} mẫu đã dịch — một mẫu judge từ chối chấm vẫn có thể "
        "đã fallback.",
    ]


    lines += [
        "",
        "## 2. Điểm theo kiểu hội thoại và độ giàu ngữ cảnh",
        "",
        "Hai chiều này tách riêng vì chúng đo hai năng lực khác nhau: hội thoại nhóm "
        "buộc Agent xử lý ngữ cảnh trộn nhiều ngôn ngữ, còn nhóm nghèo ngữ cảnh đo "
        "khả năng dịch khi không có gì để suy luận thêm.",
        "",
    ]

    for title, key in [
        ("Kiểu hội thoại", "by_chat_type"),
        ("Độ giàu ngữ cảnh", "by_context_level"),
    ]:
        lines += [
            f"**{title}**",
            "",
            f"| {title} | Số mẫu | Điểm trung bình | Tỷ lệ đạt |",
            "|---|---|---|---|",
        ]
        for value, (count, mean_score, passed) in summary[key].items():
            rate = passed / count * 100 if count else 0
            lines.append(f"| `{value}` | {count} | {mean_score:.3f} | {rate:.0f}% |")
        lines.append("")

    lines += [
        "## 3. Điểm theo nhóm tình huống",
        "",
        "Cột số mẫu quan trọng ở bảng này hơn các bảng khác: phần lớn nhóm chỉ có một "
        "đến hai mẫu, nên điểm trung bình của chúng không nói lên xu hướng.",
        "",
        "| Nhóm | Số mẫu | Điểm trung bình | Tỷ lệ đạt |",
        "|---|---|---|---|",
    ]

    for category, (count, mean_score, passed) in summary["by_category"].items():
        rate = passed / count * 100 if count else 0
        lines.append(f"| `{category}` | {count} | {mean_score:.3f} | {rate:.0f}% |")

    lines += [
        "",
        "## 4. Điểm theo cặp ngôn ngữ",
        "",
        "| Cặp | Số mẫu | Điểm trung bình | Tỷ lệ đạt |",
        "|---|---|---|---|",
    ]

    for pair, (count, mean_score, passed) in summary["by_language_pair"].items():
        rate = passed / count * 100 if count else 0
        lines.append(f"| `{pair}` | {count} | {mean_score:.3f} | {rate:.0f}% |")

    lines += [
        "",
        "## 5. Chi tiết từng mẫu",
        "",
        "| ID | Kiểu | Ngữ cảnh | Cặp | Nhóm | Điểm | Độ trễ | Câu gốc | Bản dịch hệ thống |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for r in results:
        score = f"{r['score']:.2f}" if r["score"] is not None else "—"
        flag = " (fallback)" if r["is_fallback"] else ""
        original = r["original"].replace("|", "\\|")[:45]
        actual = r["actual"].replace("|", "\\|")[:45]
        lines.append(
            f"| {r['id']} | {r['chat_type']} | {r['context_level']} | "
            f"{r['language_pair']} | {r['category']} | {score}{flag} | "
            f"{r['latency_ms']}ms | {original} | {actual} |"
        )

    failed = [r for r in results if r["score"] is not None and r["score"] < PASS_THRESHOLD]
    lines += ["", "## 6. Mẫu chưa đạt", ""]
    if not failed:
        lines.append("Không có mẫu nào dưới ngưỡng.")
    else:
        for r in failed:
            lines += [
                f"**{r['id']}** (`{r['category']}`, {r['chat_type']}, "
                f"ngữ cảnh {r['context_level']}, {r['language_pair']}) — "
                f"điểm {r['score']:.2f}",
                "",
                f"- Câu gốc: {r['original']}",
                f"- Tham chiếu: {r['expected']}",
                f"- Hệ thống: {r['actual']}",
                "",
            ]

    lines += [
        "## 7. Cách tái lập",
        "",
        "```bash",
        "python eval/run_eval.py",
        "```",
        "",
        "Đổi provider bằng biến `LLM_PROVIDER` trong `.env` rồi chạy lại để so sánh.",
        "",
    ]

    return "\n".join(lines)


def make_run_id() -> str:
    """Identify one run: sortable by time, unique between runs in the same minute."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


def golden_set_sha() -> str:
    """Fingerprint the dataset, so two runs can be told apart from two datasets.

    A comparison across an edited golden set is meaningless, and without this
    there is no way to notice it happened.
    """
    return hashlib.sha256(GOLDEN_SET.read_bytes()).hexdigest()[:12]


def write_run(results: list[dict], summary: dict, judge_provider: str | None) -> Path:
    """Persist the full run, including every translation in full.

    `report.md` is overwritten each time and truncates translations to fit a
    table, so no complete record of what the agent produced survives a second
    run. This is that record.
    """
    settings = get_settings()
    run_id = make_run_id()
    path = RESULTS_DIR / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "started_at": datetime.now(UTC).isoformat(),
                "provider": settings.llm_provider,
                "model_configured": settings.llm_model,
                "judge_provider": judge_provider or settings.llm_provider,
                "golden_set_sha": golden_set_sha(),
                "summary": summary,
                "samples": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


# How much a metric must move before the comparison calls it a change rather
# than noise. Both the translating model and the judge run at temperature 0.3,
# each sample is scored exactly once, and many breakdown buckets hold a single
# sample — so a small movement between two runs of identical code is expected.
# These are declared thresholds, not statistics: with n=53 and one observation
# per sample there is nothing here to compute a meaningful p-value from.
NOISE_AVG_SCORE = 0.05
NOISE_PASS_RATE = 5.0


def _pass_rate(summary: dict) -> float:
    """Share of scored samples that met the threshold, as a percentage."""
    return summary["passed"] / summary["scored"] * 100 if summary["scored"] else 0.0


def compare_runs(baseline: dict, current: dict, summary: dict) -> str:
    """Render the difference between a stored run and the one just finished."""
    base_summary = baseline["summary"]
    lines = [
        "",
        f"So sánh với {baseline['run_id']} "
        f"({baseline['provider']} → {current['provider']})",
        "",
    ]

    if baseline.get("golden_set_sha") != current["golden_set_sha"]:
        lines += [
            "  [!] Bộ dữ liệu đã thay đổi giữa hai lần chạy — số liệu không so sánh "
            "trực tiếp được.",
            "",
        ]

    comparisons = [
        ("Điểm trung bình", base_summary["avg_score"], summary["avg_score"],
         NOISE_AVG_SCORE, "{:.3f}"),
        ("Tỷ lệ đạt (%)", _pass_rate(base_summary), _pass_rate(summary),
         NOISE_PASS_RATE, "{:.1f}"),
        ("Độ trễ TB (ms)", base_summary["avg_latency"], summary["avg_latency"],
         None, "{:.0f}"),
        ("Độ trễ p95 (ms)", base_summary["p95_latency"], summary["p95_latency"],
         None, "{:.0f}"),
        ("Số lần fallback", base_summary["fallback"], summary["fallback"],
         None, "{:.0f}"),
    ]

    lines += ["| Chỉ số | Trước | Sau | Thay đổi |", "|---|---:|---:|---|"]
    for label, before, after, noise, fmt in comparisons:
        delta = after - before
        if noise is not None and abs(delta) < noise:
            verdict = f"trong khoảng nhiễu (±{noise:g})"
        elif f"{delta:{fmt[2:-1]}}".strip("-0.") == "":
            # Rounds to zero at the precision shown. Printing "-0" for a
            # difference of half a millisecond reads as a regression.
            verdict = "không đổi"
        else:
            verdict = f"{delta:+{fmt[2:-1]}}"
        lines.append(f"| {label} | {fmt.format(before)} | {fmt.format(after)} | {verdict} |")

    before_pass = {
        s["id"]: s["score"] is not None and s["score"] >= PASS_THRESHOLD
        for s in baseline["samples"]
    }
    flipped = [
        (s["id"], before_pass[s["id"]], s["score"] is not None and s["score"] >= PASS_THRESHOLD)
        for s in current["samples"]
        if s["id"] in before_pass
        and before_pass[s["id"]] != (s["score"] is not None and s["score"] >= PASS_THRESHOLD)
    ]

    if flipped:
        lines += ["", "Mẫu đổi trạng thái đạt/không đạt:"]
        lines += [
            f"  {sample_id}: {'đạt' if was else 'chưa đạt'} → {'đạt' if now else 'chưa đạt'}"
            for sample_id, was, now in flipped
        ]
    else:
        lines += ["", "Không mẫu nào đổi trạng thái đạt/không đạt."]

    return "\n".join(lines)


def load_run(path: Path) -> dict:
    """Read a stored run, failing with a readable message rather than a traceback."""
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy lần chạy để so sánh: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


async def main() -> int:
    """Run translation evaluation suite and output report.

    Returns:
        Exit code (0 on success, 1 on failure).
    """
    parser = argparse.ArgumentParser(description="Chấm điểm Translation Agent")

    parser.add_argument("--limit", type=int, help="Chỉ chạy N mẫu đầu")
    parser.add_argument("--no-write", action="store_true", help="Không ghi report")
    parser.add_argument(
        "--judge-provider",
        help=(
            "Provider dùng để chấm điểm. Nên đặt khác provider đang dịch, nếu không "
            "model sẽ tự chấm chính nó và điểm bị thiên vị."
        ),
    )
    parser.add_argument(
        "--compare",
        type=Path,
        help="Đường dẫn tới file JSON của một lần chạy trước để so sánh",
    )
    args = parser.parse_args()

    # Read before the run, not after: a missing file should fail immediately
    # rather than after several minutes of paid API calls.
    baseline = load_run(args.compare) if args.compare else None

    # This harness drives the graph without going through src/main.py, so it has
    # to configure logging itself or the agent's fallback records are discarded.
    configure_logging()

    samples = load_golden_set(args.limit)
    translate_provider = get_settings().llm_provider
    judge_name = args.judge_provider or translate_provider
    print(
        f"Chạy {len(samples)} mẫu · dịch={translate_provider} · chấm={judge_name}"
        + ("  [CẢNH BÁO: model tự chấm chính nó]" if judge_name == translate_provider else "")
        + "\n"
    )

    # Built once for the whole run: the configuration is identical across samples,
    # and InMemoryContextProvider already partitions by conversation_id.
    context_provider = InMemoryContextProvider()
    graph = build_translation_graph(context_provider)
    judge_llm = get_llm(provider=args.judge_provider)

    results = []
    for i, sample in enumerate(samples, 1):
        print(f"[{i}/{len(samples)}] {sample['id']} ({sample.get('category', '')})")
        try:
            result = await run_sample(sample, graph, context_provider, judge_llm)
        except Exception as exc:
            print(f"    [!] Lỗi: {type(exc).__name__}: {exc}")
            continue

        score = f"{result['score']:.2f}" if result["score"] is not None else "—"
        print(f"    điểm={score} latency={result['latency_ms']}ms")
        results.append(result)

    if not results:
        print("\nKhông chạy được mẫu nào. Kiểm tra cấu hình LLM trong .env.")
        return 1

    summary = summarize(results)
    print(
        f"\nTổng kết: {summary['passed']}/{summary['scored']} đạt · "
        f"điểm TB {summary['avg_score']:.3f} · "
        f"latency TB {summary['avg_latency']:.0f}ms · "
        f"fallback {summary['fallback']}"
    )

    print(
        f"Nhận diện khớp khai báo: {summary['detect_agreement'] * 100:.0f}% · "
        f"{summary['llm_calls']} lượt gọi LLM · "
        f"{summary['input_tokens']}/{summary['output_tokens']} token vào/ra"
    )

    if not args.no_write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            render_report(results, summary, args.judge_provider), encoding="utf-8"
        )
        print(f"Đã ghi báo cáo: {REPORT_PATH}")
        run_path = write_run(results, summary, args.judge_provider)
        print(f"Đã lưu lần chạy: {run_path}")

    if baseline is not None:
        print(
            compare_runs(
                baseline,
                {
                    "provider": get_settings().llm_provider,
                    "golden_set_sha": golden_set_sha(),
                    "samples": results,
                },
                summary,
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
