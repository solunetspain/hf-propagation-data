"""Event-time validation of next-hour predictions; never substitute today's data."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

TIMING_VERSION = "1.0-event-time"
GRACE_MINUTES = 15
RETENTION_HOURS = 48
JOURNAL_BYTES = 8 * 1024 * 1024


def utc(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def validation_capture(rows, query_results, completed_at):
    """Keep every accepted reception, not the 200-row display sample."""
    events = []
    for row in rows:
        identity = [row.get(key) for key in ("timestamp_utc", "frequency_hz", "sender_callsign", "receiver_callsign", "mode")]
        events.append({"id": hashlib.sha256(encode(identity)).hexdigest(),
                       "timestamp_utc": row["timestamp_utc"], "band": row["band"],
                       "regions": row["regions"], "receiver": row["receiver_callsign"]})
    # An interval is complete only when every query succeeded without hitting
    # the API result cap. Intersection accounts for sequential query timings.
    intervals = []
    for query in query_results.values():
        start, end = utc(query.get("started_at")), utc(query.get("finished_at"))
        if query.get("status") != "ok" or query.get("report_count", 5000) >= 5000 or not start or not end:
            intervals = []
            break
        intervals.append((end - timedelta(hours=1), start))
    start = max((item[0] for item in intervals), default=None)
    end = min((item[1] for item in intervals), default=None)
    if start and utc(completed_at):
        start = max(start, utc(completed_at) - timedelta(hours=1))
    return {"version": TIMING_VERSION, "completed_at": completed_at, "events": events,
            "coverage_start_utc": start.isoformat() if start and end and start < end else None,
            "coverage_end_utc": end.isoformat() if start and end and start < end else None}


def update_journal(path, psk, now, max_bytes=JOURNAL_BYTES):
    journal = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"events": [], "coverage": []}
    cutoff = now - timedelta(hours=RETENTION_HOURS)
    events = {item["id"]: item for item in journal["events"] if utc(item.get("timestamp_utc")) and cutoff <= utc(item["timestamp_utc"]) <= now}
    coverage = [item for item in journal["coverage"] if utc(item.get("end")) and utc(item["end"]) > cutoff]
    capture = psk.get("validation_capture", {})
    captured_at = utc(capture.get("completed_at"))
    if capture.get("version") == TIMING_VERSION and captured_at and cutoff <= captured_at <= now:
        for item in capture.get("events", []):
            stamp = utc(item.get("timestamp_utc"))
            if stamp and cutoff <= stamp <= captured_at and item.get("id"):
                candidate = dict(item, timestamp_utc=stamp.isoformat(), received_at_utc=captured_at.isoformat())
                existing = events.get(item["id"])
                if existing is None or captured_at < utc(existing["received_at_utc"]):
                    events[item["id"]] = candidate
        start, end = utc(capture.get("coverage_start_utc")), utc(capture.get("coverage_end_utc"))
        if start and end and start < end <= captured_at:
            interval = {"start": start.isoformat(), "end": end.isoformat(),
                        "received_at_utc": captured_at.isoformat(),
                        "regions": [key for key, value in psk.get("regions", {}).items() if value.get("status") == "ok"]}
            if interval not in coverage:
                coverage.append(interval)
    ordered = sorted(events.values(), key=lambda item: (item["timestamp_utc"], item["id"]))
    # If retention hits the byte ceiling, remove whole oldest timestamps and
    # shorten coverage too; absent events must never become evidence of failure.
    retained_from = max(cutoff, utc(journal.get("retained_from_utc")) or cutoff)
    while True:
        clipped = []
        for item in coverage:
            start, end = max(utc(item["start"]), retained_from), utc(item["end"])
            if start < end:
                clipped.append(dict(item, start=start.isoformat()))
        result = {"version": TIMING_VERSION, "retained_from_utc": retained_from.isoformat(),
                  "events": ordered, "coverage": clipped}
        payload = encode(result)
        if len(payload) <= max_bytes:
            break
        if not ordered:
            raise ValueError("Observation coverage metadata exceeds journal limit")
        remove = max(1, len(ordered) // 10)
        boundary = utc(ordered[remove - 1]["timestamp_utc"])
        ordered = [item for item in ordered[remove:] if utc(item["timestamp_utc"]) > boundary]
        retained_from = max(retained_from, boundary + timedelta(microseconds=1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return result


def covers(intervals, start, end, region, deadline):
    cursor = start
    for item in sorted(intervals, key=lambda value: value["start"]):
        if region not in item["regions"] or utc(item["received_at_utc"]) > deadline:
            continue
        lo, hi = utc(item["start"]), utc(item["end"])
        if lo > cursor:
            return False
        cursor = max(cursor, hi)
        if cursor >= end:
            return True
    return False


def evaluate_entry(entry, journal, now, regions, bands, classify):
    start = utc(entry.get("generated_at_utc"))
    if start is None:
        entry["validation_status"] = "invalid_prediction_timestamp"
        return
    end = start + timedelta(hours=1)
    deadline = end + timedelta(minutes=GRACE_MINUTES)
    entry["target_window"] = {"start_utc": start.isoformat(), "end_utc": end.isoformat(), "interval": "[start,end)"}
    if now < deadline:
        entry["validation_status"] = "pending"
        return
    selected = [item for item in journal["events"]
                if start <= utc(item["timestamp_utc"]) < end and utc(item["received_at_utc"]) <= deadline]
    evaluation = {}
    for region in regions:
        recommendations = entry.get("recommendations", {}).get(region, {})
        if isinstance(recommendations, list):
            first = recommendations[0] if recommendations else None
            alternative = recommendations[1] if len(recommendations) > 1 else None
        else:
            first, alternative = recommendations.get("first"), recommendations.get("alternative")
        normalize = lambda value: str(value or "").replace(" ", "").lower()
        complete = covers(journal["coverage"], start, end, region, deadline)
        evaluation[region] = {}
        for band in bands:
            observations = [item for item in selected if region in item["regions"] and item["band"] == band]
            result = classify(normalize(first) == band, normalize(alternative) == band, len(observations), complete)
            evaluation[region][band] = {
                "result": result, "observations": len(observations), "evaluated_at_utc": now.isoformat(),
                "source": "PSKReporter", "window_complete": complete,
                "reason": "incomplete_target_window" if result == "unconfirmed" else "event_time_matched",
                "first_observation_utc": min((item["timestamp_utc"] for item in observations), default=None),
                "last_observation_utc": max((item["timestamp_utc"] for item in observations), default=None),
                "evidence_sha256": hashlib.sha256(encode(sorted(item["id"] for item in observations))).hexdigest(),
            }
    entry["evaluation"] = evaluation
    entry["validation_status"] = "evaluated"
    entry["evaluation_method_version"] = TIMING_VERSION
