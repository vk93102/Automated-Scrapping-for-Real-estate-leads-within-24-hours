from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median


@dataclass
class RecPerf:
    recordingNumber: str
    ocrSeconds: float | None = None
    llmSeconds: float | None = None
    model: str | None = None


def _parse_ts(ts: str, ms: str) -> datetime:
    return datetime.strptime(f"{ts}.{ms}", "%Y-%m-%d %H:%M:%S.%f")


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = int(round((len(values) - 1) * p))
    idx = max(0, min(len(values) - 1, idx))
    return float(values[idx])


def build_report(log_path: Path) -> dict:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    start_pat = re.compile(
        r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] maricopa one-shot started \| days=(\d+) doc_code=([^ ]+) workers=(\d+)"
    )
    finish_pat = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] maricopa one-shot finished")

    start_idx = None
    start_meta = None
    for i, ln in enumerate(lines):
        m = start_pat.search(ln)
        if m:
            start_idx = i
            start_meta = {
                "startedAt": m.group(1),
                "daysWindow": int(m.group(2)),
                "docCode": m.group(3),
                "workers": int(m.group(4)),
            }

    if start_idx is None:
        raise RuntimeError(f"Could not find run start in {log_path}")

    end_idx = None
    for j in range(start_idx + 1, len(lines)):
        if finish_pat.search(lines[j]):
            end_idx = j
            break

    block = lines[start_idx : (end_idx + 1 if end_idx is not None else len(lines))]

    logline_pat = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(?P<ms>\d{3})\s+(?P<level>INFO|WARNING|ERROR)\s+(?P<msg>.*)$"
    )

    run_summary_pat = re.compile(
        r"Run summary — found=(\d+)\s+skipped=(\d+)\s+processed=(\d+)\s+failed=(\d+)\s+ocr=(\d+)\s+llm=(\d+)"
    )

    ocr_start_pat = re.compile(r"OCRing with Tesseract: (\d+)")
    ocr_quality_pat = re.compile(r"OCR quality for (\d+): confidence=([0-9.]+)")
    llm_start_pat = re.compile(r"Extracting fields via LLM for (\d+)")
    stored_pat = re.compile(r"Stored properties for (\d+) \(model=([^\)]+)\)")

    ocr_start: dict[str, datetime] = {}
    ocr_end: dict[str, datetime] = {}
    llm_start: dict[str, datetime] = {}
    llm_end: dict[str, datetime] = {}
    model_by_rec: dict[str, str] = {}

    summary = None
    first_event_ts: datetime | None = None
    last_event_ts: datetime | None = None

    for ln in block:
        m = logline_pat.match(ln)
        if not m:
            continue

        ts = _parse_ts(m.group("ts"), m.group("ms"))
        first_event_ts = ts if first_event_ts is None else first_event_ts
        last_event_ts = ts
        msg = m.group("msg")

        sm = run_summary_pat.search(msg)
        if sm:
            summary = {
                "found": int(sm.group(1)),
                "skipped": int(sm.group(2)),
                "processed": int(sm.group(3)),
                "failed": int(sm.group(4)),
                "ocr": int(sm.group(5)),
                "llm": int(sm.group(6)),
            }
            continue

        m1 = ocr_start_pat.search(msg)
        if m1:
            ocr_start[m1.group(1)] = ts
            continue

        m2 = ocr_quality_pat.search(msg)
        if m2:
            ocr_end[m2.group(1)] = ts
            continue

        m3 = llm_start_pat.search(msg)
        if m3:
            llm_start[m3.group(1)] = ts
            continue

        m4 = stored_pat.search(msg)
        if m4:
            rec = m4.group(1)
            llm_end[rec] = ts
            model_by_rec[rec] = m4.group(2)
            continue

    recordings = sorted(set(ocr_start) | set(ocr_end) | set(llm_start) | set(llm_end) | set(model_by_rec))
    perfs: list[RecPerf] = []
    for rec in recordings:
        ocr_s = (ocr_end[rec] - ocr_start[rec]).total_seconds() if rec in ocr_start and rec in ocr_end else None
        llm_s = (llm_end[rec] - llm_start[rec]).total_seconds() if rec in llm_start and rec in llm_end else None
        perfs.append(RecPerf(recordingNumber=rec, ocrSeconds=ocr_s, llmSeconds=llm_s, model=model_by_rec.get(rec)))

    ocr_vals = [p.ocrSeconds for p in perfs if isinstance(p.ocrSeconds, (int, float))]
    llm_vals = [p.llmSeconds for p in perfs if isinstance(p.llmSeconds, (int, float))]
    models_used = sorted({m for m in model_by_rec.values() if m})

    runtime_s = (last_event_ts - first_event_ts).total_seconds() if first_event_ts and last_event_ts else None

    return {
        "run": {
            **(start_meta or {}),
            "logPath": str(log_path),
            "startedAtLogTs": start_meta.get("startedAt") if start_meta else None,
            "firstEventTs": first_event_ts.isoformat() if first_event_ts else None,
            "lastEventTs": last_event_ts.isoformat() if last_event_ts else None,
            "runtimeSecondsApprox": runtime_s,
            "requestedGroqModel": "llama-3.3-70b",
            "effectiveGroqModelId": "llama-3.3-70b-versatile",
            "modelsRecordedInDb": models_used,
        },
        "counts": summary or {},
        "performance": {
            "ocrSeconds": {
                "n": len(ocr_vals),
                "avg": mean(ocr_vals) if ocr_vals else None,
                "median": median(ocr_vals) if ocr_vals else None,
                "p95": _pct(ocr_vals, 0.95),
                "max": max(ocr_vals) if ocr_vals else None,
            },
            "llmSeconds": {
                "n": len(llm_vals),
                "avg": mean(llm_vals) if llm_vals else None,
                "median": median(llm_vals) if llm_vals else None,
                "p95": _pct(llm_vals, 0.95),
                "max": max(llm_vals) if llm_vals else None,
            },
        },
        "samples": {
            "perRecordFirst5": [asdict(p) for p in perfs[:5]],
            "perRecordSlowestLLM5": [
                asdict(p)
                for p in sorted(perfs, key=lambda x: (x.llmSeconds is None, -(x.llmSeconds or 0)))[:5]
            ],
        },
    }


def main() -> None:
    log_path = Path("logs/maricopa_once.log")
    out_path = Path("output/maricopa_last2days_performance.json")
    report = build_report(log_path)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
