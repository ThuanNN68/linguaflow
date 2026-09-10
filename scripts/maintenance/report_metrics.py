#!/usr/bin/env python3
"""Report on the translation attempt log (NFR-03).

Reads `translation_attempts`, which the agent writes one row into for every
translation it tries, and answers the three questions the log exists for: how
often the primary path fails, which model actually served the traffic, and how
long each language pair takes.

Usage:
    python scripts/maintenance/report_metrics.py                # everything ever recorded
    python scripts/maintenance/report_metrics.py --since 7d     # only the last week
    python scripts/maintenance/report_metrics.py --no-write     # print, do not write

Reads the database only, calls no model, and costs no quota — unlike
`eval/run_eval.py`, which measures translation *quality* against a golden set.
This measures what production actually did.

The report is written in Vietnamese: it is a team deliverable, and project
documentation is Vietnamese by convention.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy.exc import OperationalError  # noqa: E402

from src.config import configure_logging, get_settings  # noqa: E402
from src.database import get_async_session_maker  # noqa: E402
from src.services.shared.metrics import (  # noqa: E402
    AttemptSummary,
    summarize_assistant_attempts,
    summarize_attempts,
)

REPORT_PATH = Path("data/metrics_report.md")

# NFR-01 budget for one translation, measured end to end at the service layer.
TARGET_TOTAL_MS = 1000
# Above this share of attempts falling back, the primary path needs looking at
# rather than tuning.
TARGET_FALLBACK_RATE = 0.10

_WINDOW = re.compile(r"^(\d+)([hdw])$")
_WINDOW_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def parse_window(value: str) -> timedelta:
    """Parse a window like ``7d``, ``12h`` or ``2w`` into a timedelta."""
    match = _WINDOW.match(value.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid window {value!r} — expected a number followed by h, d or w"
        )
    amount, unit = match.groups()
    return timedelta(**{_WINDOW_UNITS[unit]: int(amount)})


def _percent(value: float) -> str:
    """Format a 0-1 ratio as a percentage."""
    return f"{value * 100:.1f}%"


def _counter_table(title: str, counts: dict[str, int], total: int) -> list[str]:
    """Render a counter as a Markdown table with a share column."""
    if not counts:
        return [f"### {title}", "", "_Chưa có dữ liệu._", ""]

    lines = [f"### {title}", "", "| Giá trị | Số lượt | Tỷ lệ |", "|---|---:|---:|"]
    lines += [
        f"| `{value}` | {count} | {_percent(count / total)} |"
        for value, count in counts.items()
    ]
    return [*lines, ""]


def render_report(summary: AttemptSummary, window: str) -> str:
    """Build the Markdown report."""
    settings = get_settings()
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# BÁO CÁO VẬN HÀNH AGENT DỊCH",
        "",
        "**Dự án:** LinguaFlow",
        f"**Thời điểm chạy:** {now}",
        f"**Cửa sổ dữ liệu:** {window}",
        f"**Provider đang cấu hình:** {settings.llm_provider}",
        "",
        "> Số liệu lấy từ bảng `translation_attempts` — mỗi lượt dịch một dòng, kể cả "
        "khi không sinh ra bản dịch nào. Đây là thứ hệ thống **đã thực sự làm**, khác "
        "với `eval/run_eval.py` đo **chất lượng dịch** trên bộ golden set.",
        "",
    ]

    if summary.total == 0:
        return "\n".join(
            [
                *lines,
                "Chưa có lượt dịch nào được ghi nhận trong cửa sổ này.",
                "",
                "Gửi vài tin nhắn qua giao diện rồi chạy lại lệnh này.",
                "",
            ]
        )

    latency_ok = summary.total_ms_p95 <= TARGET_TOTAL_MS
    fallback_ok = summary.fallback_rate <= TARGET_FALLBACK_RATE

    lines += [
        "## 1. Chỉ số tổng hợp",
        "",
        "| Chỉ số | Mục tiêu | Thực tế | Trạng thái |",
        "|---|---|---|---|",
        f"| Tổng số lượt dịch | — | {summary.total} | — |",
        f"| Tỷ lệ fallback | ≤ {_percent(TARGET_FALLBACK_RATE)} | "
        f"{_percent(summary.fallback_rate)} | {'Đạt' if fallback_ok else 'Chưa đạt'} |",
        f"| p50 thời gian chờ | — | {summary.total_ms_p50:.0f}ms | — |",
        f"| p95 thời gian chờ | ≤ {TARGET_TOTAL_MS}ms | {summary.total_ms_p95:.0f}ms | "
        f"{'Đạt' if latency_ok else 'Chưa đạt'} |",
        f"| Token vào / ra | — | {summary.input_tokens} / {summary.output_tokens} | — |",
        "",
        "Tỷ lệ fallback tính trên **toàn bộ** lượt thử, gồm cả timeout, lỗi và bản dịch "
        "rỗng — những trường hợp không sinh dòng nào trong `translation_results`. Tính "
        "riêng trên các lượt thành công sẽ ra một con số đẹp hơn nhưng vô nghĩa.",
        "",
        "Thời gian chờ đo bằng wall clock ở tầng service, bao trùm cả truy vấn ngữ cảnh "
        "và overhead của LangGraph, nên rộng hơn `translation_results.latency_ms` (chỉ "
        "tính thời gian gọi model). NFR-01 nói về con số ở đây.",
        "",
        "## 2. Kết cục từng lượt dịch",
        "",
    ]
    lines += _counter_table("Phân bố `outcome`", summary.outcomes, summary.total)
    lines += _counter_table("Lý do fallback", summary.fallback_reasons, summary.total)

    lines += [
        "## 3. Nhận diện ngôn ngữ (ADR-11)",
        "",
        "`langdetect` chạy cục bộ (~2ms); chỉ khi kết quả mâu thuẫn với ngôn ngữ người "
        "gửi khai báo mới gọi LLM. Tỷ lệ `langdetect` càng cao thì tầng cục bộ càng "
        "tiết kiệm được nhiều round-trip.",
        "",
    ]
    lines += _counter_table("Phân bố `detect_method`", summary.detect_methods, summary.total)

    lines += [
        "## 4. Model thực sự phục vụ",
        "",
        "Đọc từ phản hồi của provider, không phải từ `LLM_MODEL` đã cấu hình — alias có "
        "thể trỏ sang bản khác và provider có thể định tuyến lại khi quá tải. `passthrough` "
        "không model nào chạy nên không góp mặt ở đây (24/08); mẫu số vì thế là số lượt có "
        "gọi model, không phải `total_attempts`.",
        "",
    ]
    lines += _counter_table(
        "Phân bố `model_served`",
        summary.models_served,
        sum(summary.models_served.values()),
    )

    lines += [
        "## 5. Theo cặp ngôn ngữ",
        "",
        "Ngôn ngữ nguồn lấy từ kết quả nhận diện khi có, nếu không mới dùng giá trị "
        "người gửi khai báo.",
        "",
        "| Cặp ngôn ngữ | Số lượt | p50 | p95 |",
        "|---|---:|---:|---:|",
    ]
    lines += [
        f"| `{pair}` | {stats.count} | {stats.p50_ms:.0f}ms | {stats.p95_ms:.0f}ms |"
        for pair, stats in summary.language_pairs.items()
    ]
    lines.append("")

    return "\n".join(lines)


async def main() -> int:
    """Entry point: summarise the attempt log and write the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        type=parse_window,
        help="Only count attempts from the last window, e.g. 12h, 7d, 2w",
    )
    parser.add_argument(
        "--no-write", action="store_true", help="Print the report without writing it"
    )
    args = parser.parse_args()

    configure_logging()

    since = datetime.now(UTC) - args.since if args.since else None
    window = f"{args.since} gần nhất" if args.since else "toàn bộ dữ liệu"

    session_maker = get_async_session_maker()
    try:
        async with session_maker() as session:
            summary = await summarize_attempts(session, since=since)
            assistant = await summarize_assistant_attempts(session, since=since)
    except OperationalError:
        # `create_all` runs at application startup, so a database created before
        # this table existed simply has no such table (ADR-06). Reporting is
        # read-only and deliberately does not create it as a side effect.
        print(
            "Chưa có bảng `translation_attempts` trong cơ sở dữ liệu.\n"
            "Khởi động lại server một lần (`make run`) để `create_all` tạo bảng — "
            "thao tác này không xoá dữ liệu sẵn có.",
            file=sys.stderr,
        )
        return 1

    report = render_report(summary, window) + render_assistant_report(
        assistant, window
    )
    print(report)

    if not args.no_write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(report, encoding="utf-8")
        print(f"\nĐã ghi báo cáo vào {REPORT_PATH}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


def render_assistant_report(summary, window: str) -> str:
    """Build the Assistant Agent's half of the report.

    A separate section rather than more rows in the translation tables. The two
    agents are measured on different things, and a single table carrying both
    would be mostly blank whichever agent a reader came for.
    """
    settings = get_settings()
    provider, model = settings.resolve_assistant_llm()

    lines = [
        "",
        "---",
        "",
        "# BÁO CÁO VẬN HÀNH AGENT TRỢ LÝ",
        "",
        f"**Cửa sổ dữ liệu:** {window}",
        f"**Model đang cấu hình:** {provider}/{model or 'mặc định của provider'}",
        "",
        "> Số liệu lấy từ bảng `assistant_attempts` — mỗi lượt chạy một dòng, **kể cả "
        "những lượt không sinh ra gì**: bị từ chối vì thiếu quyền, hỏi ngược lại, hoặc "
        "chạy hết mà không tìm thấy gì. Đó chính là mẫu số: tỉ lệ đề xuất được duyệt "
        "tính riêng trên các lượt đã tới cổng duyệt thì không phải là một tỉ lệ.",
        "",
    ]

    if summary.total == 0:
        lines.append("Chưa có lượt chạy nào của agent trợ lý trong cửa sổ này.")
        return "\n".join(lines)

    lines += _counter_table("Kết cục", summary.outcomes, summary.total)

    reached_gate = summary.outcomes.get("proposed", 0) + summary.outcomes.get(
        "executed", 0
    )
    lines += [
        "## Đề xuất và cổng duyệt",
        "",
        f"- Số lượt tới cổng duyệt: **{reached_gate}** / {summary.total} "
        f"({_percent(reached_gate / summary.total)})",
        f"- Đề xuất đã sinh: **{summary.proposals_created}**",
        f"- Đề xuất được duyệt ngay trong lượt chạy: **{summary.proposals_executed}**",
        "",
        "> Đề xuất được duyệt sau đó qua endpoint REST **không** tính ở đây: nó thuộc về "
        "request đó, không thuộc lượt chạy đã kết thúc (ADR-32). Chênh lệch giữa hai con "
        "số trên là số đề xuất đang chờ người dùng, chứ không phải số bị bỏ qua.",
        "",
    ]

    if summary.tool_calls:
        failed_share = summary.tools_failed / summary.tool_calls
        lines += [
            "## Công cụ",
            "",
            f"- Tổng lời gọi: **{summary.tool_calls}**, hỏng **{summary.tools_failed}** "
            f"({_percent(failed_share)})",
            "",
            "| Công cụ | Số lần gọi |",
            "|---|---:|",
        ]
        lines += [
            f"| `{name}` | {count} |"
            for name, count in sorted(
                summary.tool_counts.items(), key=lambda item: -item[1]
            )
        ]
        lines.append("")

    lines += [
        "## Số vòng lập kế hoạch",
        "",
        "| Số vòng | Số lượt |",
        "|---:|---:|",
    ]
    lines += [
        f"| {rounds} | {count} |" for rounds, count in sorted(summary.replans.items())
    ]
    lines += [
        "",
        "> Tất cả dồn ở 1 nghĩa là vòng replan chưa đáng giá tiền của nó; tất cả dồn ở "
        "trần nghĩa là các lượt chạy đang bị cắt giữa chừng.",
        "",
    ]

    recall_share = (
        summary.memory_recalled / summary.memory_lines if summary.memory_lines else 0.0
    )
    lines += [
        "## Trí nhớ",
        "",
        f"- Số dòng ngữ cảnh đã nạp: **{summary.memory_lines}**",
        f"- Trong đó do truy hồi ngữ nghĩa mang về: **{summary.memory_recalled}** "
        f"({_percent(recall_share)})",
        "",
        "> Con số thứ hai bằng 0 ở mọi lượt nghĩa là `assistant_chunks` đang rỗng — "
        "chạy `make assistant-backfill`. Không chỗ nào khác trong hệ thống báo điều này.",
        "",
        "## Độ trễ",
        "",
        f"- Trung bình **{summary.avg_ms:.0f}ms**, p95 **{summary.p95_ms:.0f}ms**",
        "",
    ]

    if summary.errors:
        lines += _counter_table("Nguyên nhân dừng", summary.errors, summary.total)

    return "\n".join(lines)
