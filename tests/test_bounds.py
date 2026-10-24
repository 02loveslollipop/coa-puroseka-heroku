import pytest
from services.ontology.query import validate_sql, QueryRejected


def test_reject_excessive_query():
    with pytest.raises(QueryRejected):
        validate_sql("SELECT " + ("1," * 10000) + "1", {"events"})


def test_boolean_predicates_and_window_functions():
    query = "WITH daily AS (SELECT event_id,rank,score,observed_at,lag(score) OVER (PARTITION BY event_id,rank ORDER BY observed_at) AS previous_score FROM ranking_observations WHERE event_id=1 AND rank=100) SELECT * FROM daily"
    assert "LAG" in validate_sql(query, {"ranking_observations"})
