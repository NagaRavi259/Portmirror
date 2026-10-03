"""CSV rendering for history and audit exports. Pure functions, no I/O, so they're unit-testable.

Cells that come from user-controlled text (forward names, audit targets, descriptions) are
neutralised against spreadsheet formula injection: a cell starting with = + - @ or a tab/CR is
prefixed with a single quote, so opening the export in Excel or LibreOffice shows the text instead
of running it as a formula. Numbers are never prefixed - a negative value is still a number.
"""
import csv
import io
import json
from datetime import datetime, timezone

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, (dict, list)):
        v = json.dumps(v, separators=(",", ":"), default=str)
    s = str(v)
    if s.startswith(_DANGEROUS):
        return "'" + s
    return s


def to_csv(headers: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(headers)
    for row in rows:
        w.writerow([cell(v) for v in row])
    return buf.getvalue()


def audit_csv(entries: list[dict]) -> str:
    return to_csv(["time_utc", "actor", "action", "target", "detail"],
                  [[e["ts"], e["actor"], e["action"], e["target"], e["detail"]] for e in entries])


def history_csv(points: list[dict], step_s: int | None) -> str:
    return to_csv(["time_utc", "bucket_seconds", "new_per_second", "in_bps", "out_bps", "live_max"],
                  [[datetime.fromtimestamp(p["t"], timezone.utc).isoformat(), step_s, p.get("new_per_s"), p.get("in_bps"), p.get("out_bps"), p.get("live")]
                   for p in points])
