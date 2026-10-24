import pytest
from services.ontology.query import validate_sql, QueryRejected

TABLES = {"events", "cards", "event_cards", "gachas", "characters"}


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE data.events",
        "SELECT 1; DELETE FROM data.events",
        "SELECT * INTO stolen FROM data.events",
        "SELECT * FROM pg_catalog.pg_roles",
        "SELECT pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "WITH x AS (DELETE FROM data.events RETURNING *) SELECT * FROM x",
        "SELECT * FROM data.events FOR UPDATE",
        "SELECT * FROM pg_catalog.events",
        "SELECT set_config('search_path','public',true)",
        "SELECT * FROM generate_series(1,1000000)",
        "WITH x AS (WITH auth_accounts AS (SELECT 1) SELECT * FROM auth_accounts) SELECT * FROM auth_accounts",
    ],
)
def test_reject_unsafe_queries(query):
    with pytest.raises(QueryRejected):
        validate_sql(query, TABLES)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT COUNT(*) FROM events",
        "SELECT DISTINCT c.id FROM cards c JOIN event_cards ec ON ec.card_id=c.id WHERE ec.event_id=1",
        "SELECT to_timestamp(start_at / 1000.0) AT TIME ZONE 'UTC' FROM events WHERE id=1",
        "WITH x AS (SELECT id FROM events) SELECT * FROM x",
    ],
)
def test_accept_queries(query):
    assert "coa_puroseka." in validate_sql(query, TABLES)


def test_cte_can_shadow_a_public_table_without_being_requalified():
    query = validate_sql(
        "WITH events AS (SELECT id FROM coa_puroseka.events) SELECT * FROM events",
        TABLES,
    )
    assert query.endswith("SELECT * FROM events")
