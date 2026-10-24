"""Demo TTL, prompt minimization and accounting isolation against PostgreSQL."""

from datetime import timedelta
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from services.chat import control
from shared.demo import DEMO_USERNAME


def test_demo_retention_is_owner_scoped_and_preserves_quota(budget_db):
    url, name = budget_db
    with psycopg.connect(url) as conn:
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.chat_policies VALUES(%s,true,true,25000,21600)"
            ).format(sql.Identifier(name)),
            (DEMO_USERNAME,),
        )
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.chat_budgets(username,used_tokens,resets_at) VALUES(%s,1234,%s)"
            ).format(sql.Identifier(name)),
            (DEMO_USERNAME, control.utcnow() + timedelta(hours=6)),
        )
    request = {"question": "private input", "history": [{"content": "private history"}]}
    job = control.create_job(DEMO_USERNAME, request)
    control.finish_job(
        job,
        {
            "answer": "Observed score: 42",
            "question": "private input",
            "_review": request,
        },
    )
    result = control.get_job(DEMO_USERNAME, job)
    assert result["result"] == {"answer": "Observed score: 42"}
    other = control.create_job("demo", request)
    old = str(uuid4())
    with psycopg.connect(url) as conn:
        assert (
            conn.execute(
                sql.SQL("SELECT request FROM {}.chat_jobs WHERE id=%s").format(
                    sql.Identifier(name)
                ),
                (job,),
            ).fetchone()[0]
            == {}
        )
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.chat_jobs(id,username,request,result,created_at) VALUES(%s,%s,%s,%s,%s)"
            ).format(sql.Identifier(name)),
            (
                old,
                DEMO_USERNAME,
                Jsonb(request),
                Jsonb({"_review": request}),
                control.utcnow() - timedelta(hours=25),
            ),
        )
        conn.execute(
            sql.SQL("UPDATE {}.chat_jobs SET created_at=%s WHERE id=%s").format(
                sql.Identifier(name)
            ),
            (control.utcnow() - timedelta(hours=25), other),
        )
    assert control.get_job(DEMO_USERNAME, old) is None  # inaccessible even before sweep
    # Simulate a still-recent job from before the privacy policy was deployed.
    with psycopg.connect(url) as conn:
        conn.execute(
            sql.SQL("UPDATE {}.chat_jobs SET request=%s,result=%s WHERE id=%s").format(
                sql.Identifier(name)
            ),
            (
                Jsonb(request),
                Jsonb(
                    {
                        "answer": "Observed score: 42",
                        "question": "private input",
                        "_review": request,
                    }
                ),
                job,
            ),
        )
    before = control.usage(DEMO_USERNAME)
    control.clean_demo_jobs()
    after = control.usage(DEMO_USERNAME)
    assert before == after
    assert control.get_job("demo", other) is not None
    assert control.get_job(DEMO_USERNAME, job)["result"] == {
        "answer": "Observed score: 42"
    }
    with psycopg.connect(url) as conn:
        assert (
            conn.execute(
                sql.SQL("SELECT count(*) FROM {}.chat_jobs WHERE id=%s").format(
                    sql.Identifier(name)
                ),
                (old,),
            ).fetchone()[0]
            == 0
        )
