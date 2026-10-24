"""Ranking narrative must preserve numeric order and observed coverage."""

from services.chat.evidence import ranking_answer, is_ranking_query


def test_score_comparison_cannot_reverse_numeric_order():
    answer = ranking_answer(
        {
            "rows": [
                {"event_id": 1, "rank": 100, "score": 27149542},
                {"event_id": 2, "rank": 100, "score": 19126873},
            ]
        }
    )
    assert "event 1 has the higher" in answer and "8,022,669 points" in answer
    assert "27,149,542" in answer and "19,126,873" in answer
    assert "milestone" not in answer and "likely" not in answer


def test_elapsed_comparison_uses_shared_day_and_retains_gaps():
    rows = [
        {"event_id": 1, "rank": 100, "event_day": 2, "score": 10},
        {"event_id": 1, "rank": 100, "event_day": 4, "score": 40},
        {"event_id": 2, "rank": 100, "event_day": 2, "score": 20},
        {"event_id": 2, "rank": 100, "event_day": 4, "score": 30},
        {"event_id": 2, "rank": 100, "event_day": 5, "score": 50},
    ]
    answer = ranking_answer({"rows": rows})
    assert "day 4" in answer and "event 1 has the higher" in answer
    assert "| 1 | 100 | 3 |" not in answer
    assert "24-hour intervals" in answer


def test_daily_first_change_null_and_truncation_are_visible():
    answer = ranking_answer(
        {
            "rows": [
                {
                    "event_id": 1,
                    "utc_day": "2020-10-11",
                    "score_change_since_previous_observed_day": None,
                    "elapsed_calendar_days": None,
                }
            ],
            "truncated": True,
        }
    )
    assert "UTC calendar days" in answer and "—" in answer and "truncated" in answer


def test_no_comparison_of_different_ranks_or_duplicate_endpoints():
    answer = ranking_answer(
        {
            "rows": [
                {"event_id": 1, "rank": 100, "score": 50},
                {"event_id": 2, "rank": 1000, "score": 10},
            ]
        }
    )
    assert "higher" not in answer
    assert "No matching" in ranking_answer({"rows": []})
    assert is_ranking_query(
        "WITH x AS (SELECT * FROM coa_puroseka.ranking_observations) SELECT * FROM x"
    )
    assert not is_ranking_query("SELECT count(*) FROM coa_puroseka.events")


def test_ranking_response_uses_only_one_model_call(monkeypatch):
    import asyncio
    from services.chat.inference import infer
    from services.chat import tracing

    calls = []

    def rpc(service, method, path, **kwargs):
        if path == "/internal/context":
            return {
                "catalog": {"version": "test"},
                "physical_schema": {"ranking_observations": {"score": "bigint"}},
            }
        return {
            "sql": "SELECT * FROM coa_puroseka.ranking_observations",
            "rows": [
                {"event_id": 1, "rank": 100, "score": 27149542},
                {"event_id": 2, "rank": 100, "score": 19126873},
            ],
            "truncated": False,
        }

    async def model(messages, json_mode=False):
        calls.append(messages)
        return (
            '{"sql":"SELECT * FROM coa_puroseka.ranking_observations","reason":"Compare observed scores"}',
            {"total_tokens": 10},
        )

    monkeypatch.setattr("services.chat.inference.call", rpc)
    monkeypatch.setattr("services.chat.inference.complete", model)
    trace = []
    token = tracing.calls.set(trace)
    try:
        result = asyncio.run(
            infer("Compare the returned scores", "base", [], presentation="chat")
        )
    finally:
        tracing.calls.reset(token)
    assert result["status"] == "ok" and len(calls) == 1
    assert "event 1 has the higher" in result["answer"]
    assert any(call["name"] == "format_ranking_evidence" for call in trace)


def test_fractional_comparison_does_not_round_evidence():
    answer = ranking_answer(
        {
            "rows": [
                {"event_id": 1, "rank": 100, "score": 42.5},
                {"event_id": 2, "rank": 100, "score": 41.0},
            ]
        }
    )
    assert "42.5" in answer and "1.5 points" in answer


def test_provenance_stays_in_review_evidence_without_crowding_answer():
    rows = [
        {
            "event_id": 1,
            "rank": 100,
            "score": 50,
            "row_key": "private-row-key",
            "source_file": "internal/source.json",
            "raw_record": {"original": True},
        }
    ]
    answer = ranking_answer({"rows": rows})
    assert "private-row-key" not in answer and "internal/source.json" not in answer
    assert rows[0]["raw_record"] == {"original": True}
    assert "private-row-key" in ranking_answer(
        {"rows": [{"row_key": "private-row-key"}]}
    )
