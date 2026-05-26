from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_div(left: float, right: float) -> float:
    return round(left / right, 4) if right else 0.0


def _extract(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    summary = payload.get("summary") or {}
    trace = summary.get("db_trace") or {}
    events = _num(summary.get("events_sent_total"))
    commands = _num(summary.get("commands_sent_total"))
    return {
        "path": str(path),
        "status": payload.get("status"),
        "elapsed_seconds": payload.get("elapsed_seconds"),
        "events": int(events),
        "commands": int(commands),
        "throughput_eps": summary.get("throughput_events_per_sec"),
        "command_success_rate": summary.get("command_success_rate"),
        "latency_avg_ms": summary.get("latency_avg_ms"),
        "latency_p50_ms": summary.get("latency_p50_ms"),
        "latency_p95_ms": summary.get("latency_p95_ms"),
        "latency_p99_ms": summary.get("latency_p99_ms"),
        "db_timeouts": summary.get("db_timeouts"),
        "db_slow_queries": summary.get("db_slow_queries"),
        "chat_history_failures": summary.get("chat_history_failures"),
        "statistics_flush_failures": summary.get("statistics_flush_failures"),
        "db_calls": int(_num(trace.get("calls"))),
        "db_reads": int(_num(trace.get("reads"))),
        "db_writes": int(_num(trace.get("writes"))),
        "db_scripts": int(_num(trace.get("scripts"))),
        "db_calls_per_event": _safe_div(_num(trace.get("calls")), events),
        "db_reads_per_event": _safe_div(_num(trace.get("reads")), events),
        "db_writes_per_event": _safe_div(_num(trace.get("writes")), events),
        "db_calls_per_command": _safe_div(_num(trace.get("calls")), commands),
        "db_writes_per_command": _safe_div(_num(trace.get("writes")), commands),
        "db_avg_elapsed_ms": trace.get("avg_elapsed_ms"),
        "db_avg_wait_ms": trace.get("avg_wait_ms"),
        "db_max_elapsed_ms": trace.get("max_elapsed_ms"),
        "db_max_wait_ms": trace.get("max_wait_ms"),
        "db_max_active": trace.get("max_active"),
        "db_max_waiting": trace.get("max_waiting"),
        "db_connection_creates": trace.get("connection_creates"),
        "db_top_tables": trace.get("top_tables", [])[:12],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", nargs="+")
    parser.add_argument("--output")
    args = parser.parse_args()
    rows = [_extract(Path(item).resolve()) for item in args.reports]
    payload = {"reports": rows}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
