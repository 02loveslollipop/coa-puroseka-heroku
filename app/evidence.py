"""Present ranking evidence without asking a model to invent an interpretation."""

from decimal import Decimal
import json
from sqlglot import parse_one, exp

LABELS = {
    "event_id": "Event",
    "rank": "Rank",
    "event_day": "Elapsed event day",
    "utc_day": "UTC day",
    "observed_at": "Observed at",
    "score": "Observed score",
    "last_observed_score": "Last observed score",
    "competition_end": "Competition end",
    "score_change_since_previous_observed_day": "Change since previous observed day",
    "elapsed_calendar_days": "Calendar days since previous observation",
}


def is_ranking_query(statement):
    return any(
        t.name == "ranking_observations"
        for t in parse_one(statement, read="postgres").find_all(exp.Table)
    )


def text(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def number(value):
    rendered = format(Decimal(str(value)), ",f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def comparison(rows, score):
    if (
        not rows
        or not score
        or any("event_id" not in r or "rank" not in r for r in rows)
    ):
        return ""
    if len({r["rank"] for r in rows}) != 1:
        return ""
    events = list(dict.fromkeys(r["event_id"] for r in rows))
    if len(events) != 2:
        return ""
    prefix = ""
    if all(isinstance(r.get("event_day"), int) for r in rows):
        by_day = {(r["event_id"], r["event_day"]): r for r in rows}
        if len(by_day) != len(rows):
            return ""
        common = {r["event_day"] for r in rows if r["event_id"] == events[0]} & {
            r["event_day"] for r in rows if r["event_id"] == events[1]
        }
        if not common:
            return ""
        day = max(common)
        left, right = by_day[events[0], day], by_day[events[1], day]
        prefix = (
            f"At the latest shared event day in the returned observations (day {day}), "
        )
    elif len(rows) == 2 and not any("utc_day" in r or "event_day" in r for r in rows):
        left, right = rows
    else:
        return ""
    if any(
        isinstance(r.get(score), bool)
        or not isinstance(r.get(score), (int, float, Decimal))
        for r in (left, right)
    ):
        return ""
    a, b = Decimal(str(left[score])), Decimal(str(right[score]))
    if a == b:
        return (
            prefix
            + f"events {text(left['event_id'])} and {text(right['event_id'])} have the same returned score ({number(a)})."
        )
    high, low = (left, right) if a > b else (right, left)
    return (
        prefix
        + f"event {text(high['event_id'])} has the higher returned rank {text(high['rank'])} score: {number(high[score])}, compared with {number(low[score])} for event {text(low['event_id'])}. The difference is {number(abs(a - b))} points."
    )


def ranking_answer(evidence):
    rows = evidence["rows"]
    if not rows:
        return "No matching ranking observations were returned for this question."
    columns = list(dict.fromkeys(key for row in rows for key in row))
    score = next(
        (key for key in ("score", "last_observed_score") if key in columns), None
    )
    parts = ["Recorded ranking results from the available history."]
    summary = comparison(rows, score)
    if summary:
        parts.append(summary)
    if "event_day" in columns:
        parts.append(
            "Elapsed event days are 24-hour intervals from each event’s start. They differ from UTC calendar dates."
        )
    if "utc_day" in columns:
        parts.append("The daily results use UTC calendar days.")
    if (
        "elapsed_calendar_days" in columns
        or "score_change_since_previous_observed_day" in columns
    ):
        parts.append(
            "Changes span the previous observed endpoint. A dash marks a missing or first value; gaps are not zero-score days."
        )
    parts.append(
        "| "
        + " | ".join(
            text(LABELS.get(c, c.replace("_", " ").capitalize())) for c in columns
        )
        + " |\n| "
        + " | ".join("---" for _ in columns)
        + " |\n"
        + "\n".join(
            "| " + " | ".join(text(row.get(c)) for c in columns) + " |" for row in rows
        )
    )
    if evidence.get("truncated"):
        parts.append(
            "The returned evidence is truncated; the table shows only the available result slice."
        )
    parts.append(
        "Source: event ranking history. Last observed scores may precede the competition end."
    )
    return "\n\n".join(parts)
