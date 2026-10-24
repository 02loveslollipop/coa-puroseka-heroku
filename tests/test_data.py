import json
from pathlib import Path
import pytest
from rdflib import Graph
from app.config import artifact, ARTIFACTS, settings

pytestmark = pytest.mark.skipif(
    not (ARTIFACTS / "catalog.json").exists()
    and settings().get("ARTIFACT_STORE") != "database",
    reason="Requires private prepared ontology artifacts",
)


def test_relationship_integrity():
    c = artifact("catalog.json")
    assert len(c["relationships"]) in (14, 15)
    assert all(r["unresolved_count"] == 0 for r in c["relationships"])
    for r in c["relationships"]:
        assert r["column"] in c["tables"][r["source"]]["fields"]
        assert r["target_column"] in c["tables"][r["target"]]["fields"]


def test_ontology():
    g = Graph().parse(ARTIFACTS / "ontology.ttl")
    assert len(g) > 700
    assert artifact("catalog.json")["version"] in str(g.serialize())


def test_source_coverage():
    m = artifact("manifest.json")
    assert sum(x["records"] for x in m["sources"]) == 3200
    assert m["row_counts"]["gacha_details"] == 199631
    assert m["row_counts"]["card_parameters"] == 186420
    assert len({x["sha256"] for x in m["sources"]}) == 5


def test_ranking_manifest():
    if not (ARTIFACTS / "ranking_manifest.json").exists():
        pytest.skip("Full rankings not prepared")
    m = artifact("ranking_manifest.json")
    assert m["events"] == 142 and m["source_file_count"] == 3894
    assert m["source_directory_count"] == 180
    assert (
        len({x["file"].split("/")[0] for x in m["sources"]})
        == m["directories_with_files"]
        == 177
    )
    assert (
        m["row_count"]
        == sum(m["rows_per_event"].values())
        == sum(m["rows_per_rank"].values())
    )
    assert len(m["sources"]) == 3894


def test_few_shot_safety_and_mappings():
    from app.query import validate_sql

    examples = json.loads(
        (Path(__file__).resolve().parents[1] / "semantic/few_shots.json").read_text()
    )
    tables = set(artifact("catalog.json")["tables"]) | {"ranking_observations"}
    for example in examples:
        assert validate_sql(example["sql"], tables)
