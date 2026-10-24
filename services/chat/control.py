"""Persistent application state, isolated from the SELECT-only data connection."""

from datetime import timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from shared.control_database import control_connection, utcnow
from shared.database import namespace
from shared.demo import DEMO_USERNAME, DEMO_RETENTION_HOURS


def interrupt_jobs():
    # One chat process owns jobs. Restarts never replay a paid model call.
    with control_connection() as conn:
        conn.execute(
            f"UPDATE {namespace()}.chat_jobs SET state='expired' WHERE state='running'"
        )


def clean_demo_jobs():
    """TTL maintenance in the existing web process; accounting is independent."""
    with control_connection() as conn:
        conn.execute(
            f"DELETE FROM {namespace()}.chat_jobs WHERE username=%s AND created_at <= %s",
            (DEMO_USERNAME, utcnow() - timedelta(hours=DEMO_RETENTION_HOURS)),
        )
        # Also remove sensitive fields from earlier demo jobs, including jobs
        # written before this retention policy was deployed.
        conn.execute(
            f"UPDATE {namespace()}.chat_jobs SET request='{{}}'::jsonb,"
            "result=result-'_review'-'question' WHERE username=%s "
            "AND (request <> '{}'::jsonb OR result ? '_review' OR result ? 'question')",
            (DEMO_USERNAME,),
        )
        conn.execute(
            f"DELETE FROM {namespace()}.chat_reservations WHERE username=%s "
            "AND state='charged' AND created_at <= %s",
            (DEMO_USERNAME, utcnow() - timedelta(hours=DEMO_RETENTION_HOURS)),
        )


def account(username):
    with control_connection() as conn:
        return conn.execute(
            f"SELECT * FROM {namespace()}.chat_policies WHERE username=%s", (username,)
        ).fetchone()


def _budget(conn, username):
    """Lock order is always account, then budget; all accounting uses this path."""
    user = conn.execute(
        f"SELECT * FROM {namespace()}.chat_policies WHERE username=%s FOR UPDATE",
        (username,),
    ).fetchone()
    if not user or not user["enabled"]:
        raise ValueError("Account unavailable")
    budget = conn.execute(
        f"SELECT * FROM {namespace()}.chat_budgets WHERE username=%s FOR UPDATE",
        (username,),
    ).fetchone()
    now = utcnow()
    if now >= budget["resets_at"]:
        budget = conn.execute(
            f"UPDATE {namespace()}.chat_budgets SET used_tokens=0,started_at=%s,resets_at=%s "
            "WHERE username=%s RETURNING *",
            (now, now + timedelta(seconds=user["window_seconds"]), username),
        ).fetchone()
    # Interrupted requests remain charged conservatively instead of resetting on restart.
    stale = conn.execute(
        f"UPDATE {namespace()}.chat_reservations SET state='charged',charged_tokens=amount "
        "WHERE username=%s AND state='active' AND created_at < %s RETURNING amount",
        (username, now - timedelta(minutes=10)),
    ).fetchall()
    if stale:
        total = sum(x["amount"] for x in stale)
        budget = conn.execute(
            f"UPDATE {namespace()}.chat_budgets SET reserved_tokens=reserved_tokens-%s,"
            "used_tokens=used_tokens+%s WHERE username=%s RETURNING *",
            (total, total, username),
        ).fetchone()
    return user, budget


def usage(username):
    with control_connection() as conn:
        user, budget = _budget(conn, username)
        return {
            "enabled": user["limit_enabled"],
            "token_limit": user["token_limit"],
            "window_seconds": user["window_seconds"],
            "used_tokens": budget["used_tokens"],
            "reserved_tokens": budget["reserved_tokens"],
            "remaining_tokens": max(
                0,
                user["token_limit"] - budget["used_tokens"] - budget["reserved_tokens"],
            ),
            "resets_at": budget["resets_at"],
        }


class BudgetExceeded(Exception):
    def __init__(self, resets_at):
        self.retry_after = max(1, int((resets_at - utcnow()).total_seconds()) + 1)
        super().__init__(
            "There is not enough token allowance to start this response. "
            "Try again after the allowance resets."
        )


def reserve(username, amount):
    with control_connection() as conn:
        user, budget = _budget(conn, username)
        if user["limit_enabled"] and (
            budget["used_tokens"] + budget["reserved_tokens"] + amount
            > user["token_limit"]
        ):
            raise BudgetExceeded(budget["resets_at"])
        reservation = str(uuid4())
        conn.execute(
            f"INSERT INTO {namespace()}.chat_reservations (id,username,amount) VALUES (%s,%s,%s)",
            (reservation, username, amount),
        )
        conn.execute(
            f"UPDATE {namespace()}.chat_budgets SET reserved_tokens=reserved_tokens+%s WHERE username=%s",
            (amount, username),
        )
        return reservation


def settle(username, reservation, tokens):
    with control_connection() as conn:
        _budget(conn, username)
        row = conn.execute(
            f"SELECT * FROM {namespace()}.chat_reservations WHERE id=%s AND username=%s FOR UPDATE",
            (reservation, username),
        ).fetchone()
        if not row or row["state"] != "active":
            return
        charged = row["amount"] if tokens is None else max(0, int(tokens))
        conn.execute(
            f"UPDATE {namespace()}.chat_reservations SET state='charged',charged_tokens=%s WHERE id=%s",
            (charged, reservation),
        )
        conn.execute(
            f"UPDATE {namespace()}.chat_budgets SET reserved_tokens=reserved_tokens-%s,"
            "used_tokens=used_tokens+%s WHERE username=%s",
            (row["amount"], charged, username),
        )


def update_limit(username, enabled, token_limit, window_seconds):
    with control_connection() as conn:
        _budget(conn, username)
        conn.execute(
            f"UPDATE {namespace()}.chat_policies SET limit_enabled=%s,token_limit=%s,window_seconds=%s "
            "WHERE username=%s",
            (enabled, token_limit, window_seconds, username),
        )
    # Changes never erase the active window's consumption.
    return usage(username)


def create_job(username, request):
    with control_connection() as conn:
        conn.execute(
            f"SELECT username FROM {namespace()}.chat_policies WHERE username=%s FOR UPDATE",
            (username,),
        )
        pending = conn.execute(
            f"SELECT count(*) AS n FROM {namespace()}.chat_jobs WHERE username=%s AND state='running' "
            "AND created_at > %s",
            (username, utcnow() - timedelta(minutes=10)),
        ).fetchone()["n"]
        if pending >= 2:
            raise ValueError(
                "Two responses are already in progress. Wait for one to finish."
            )
        job = str(uuid4())
        conn.execute(
            f"INSERT INTO {namespace()}.chat_jobs(id,username,request) VALUES (%s,%s,%s)",
            (job, username, Jsonb({} if username == DEMO_USERNAME else request)),
        )
        return job


def update_job_activity(job, activity):
    with control_connection() as conn:
        conn.execute(
            f"UPDATE {namespace()}.chat_jobs SET result=%s WHERE id=%s AND state='running'",
            (Jsonb({"_activity": activity}), job),
        )


def finish_job(job, result):
    demo_result = {k: v for k, v in result.items() if k not in {"_review", "question"}}
    with control_connection() as conn:
        conn.execute(
            f"UPDATE {namespace()}.chat_jobs SET state='done',"
            "result=CASE WHEN username=%s THEN %s ELSE %s END WHERE id=%s",
            (DEMO_USERNAME, Jsonb(demo_result), Jsonb(result), job),
        )


def get_job(username, job):
    with control_connection() as conn:
        row = conn.execute(
            f"SELECT id,state,result,created_at FROM {namespace()}.chat_jobs "
            "WHERE id=%s AND username=%s AND (username<>%s OR created_at>%s)",
            (
                job,
                username,
                DEMO_USERNAME,
                utcnow() - timedelta(hours=DEMO_RETENTION_HOURS),
            ),
        ).fetchone()
        if (
            row
            and row["state"] == "running"
            and utcnow() - row["created_at"] > timedelta(minutes=10)
        ):
            row.update(state="expired", result=None)
        return row
