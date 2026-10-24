"""Merge curated ranking semantics, few-shots and prepared artifacts into a fixed release."""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rdflib import Graph, Namespace, RDF, RDFS, OWL, Literal
from app.config import artifact, ARTIFACTS


def run():
    c = artifact("catalog.json")
    ranking = json.loads((ROOT / "semantic/rankings.json").read_text())
    m = artifact("ranking_manifest.json")
    ranking["row_count"] = m["row_count"]
    ranking["source_files"] = ["event_rank_snapshots/*/rank_*.json"]
    c["tables"]["ranking_observations"] = ranking
    for rule in ranking.pop("rules"):
        if rule not in c["rules"]:
            c["rules"].append(rule)
    for t, e in c["tables"].items():
        if not e.get("source_files") and t not in (
            "entity_labels",
            "ranking_observations",
        ):
            mapping = {
                "gacha_": "gachas.json",
                "card_": "cards.json",
                "event_": "events.json",
            }
            e["source_files"] = [
                next((v for k, v in mapping.items() if t.startswith(k)), t + ".json")
            ]
    c["data_coverage"] = {
        "metadata_events": 180,
        "ranking_events_with_observations": m["events"],
        "ranking_source_event_directories": m["source_directory_count"],
        "ranking_event_directories_with_files": m["directories_with_files"],
        "ranking_empty_directories": m["empty_directories"],
        "ranking_events_without_observations": sorted(
            set(range(1, 181)) - {int(x) for x in m["rows_per_event"]}
        ),
        "null_source_entries_skipped": m["null_entries_skipped"],
        "duplicate_series_timestamps_preserved": m["duplicate_series_timestamps"],
    }
    c["version"] = "puroseka-1.1.0"
    c["ranking_source_files"] = m["source_file_count"]
    c["description"] = (
        "Fixed ontology and semantic context for all supplied Puroseka metadata and historical ranking observations."
    )
    relation = {
        "source": "ranking_observations",
        "column": "event_id",
        "target": "events",
        "target_column": "id",
        "name": "observedInEvent",
        "definition": "Historical ranking observation belongs to the referenced event.",
        "unresolved_count": 0,
        "unresolved_values": {},
        "status": "validated",
    }
    import gzip

    with gzip.open(ARTIFACTS / "dataset.json.gz", "rt") as f:
        dataset = json.load(f)
    event_ids = {str(r["id"]) for r in dataset["tables"]["events"]}
    missing = set(m["rows_per_event"]) - event_ids
    if missing:
        raise ValueError("Ranking event IDs missing from metadata: " + str(missing))
    if not any(r["name"] == relation["name"] for r in c["relationships"]):
        c["relationships"].append(relation)
    # A displayed leader card is optional; validate actual IDs before asserting its relationship.
    (ARTIFACTS / "catalog.json").write_text(json.dumps(c, ensure_ascii=False, indent=2))
    manifest = artifact("manifest.json")
    manifest.update(
        version=c["version"],
        ranking_source_files=m["source_file_count"],
        ranking_events=m["events"],
        ranking_observations=m["row_count"],
    )
    manifest["row_counts"]["ranking_observations"] = m["row_count"]
    manifest["relationships"] = c["relationships"]
    (ARTIFACTS / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    shots = json.loads((ROOT / "semantic/few_shots.json").read_text())
    (ARTIFACTS / "few_shots.json").write_text(
        json.dumps(shots, ensure_ascii=False, indent=2)
    )
    ns = Namespace("https://puroseka.example/ontology/")
    g = Graph().parse(ARTIFACTS / "ontology.ttl")
    g.set((ns.Ontology, OWL.versionInfo, Literal(c["version"])))
    for table in ("ranking_observations", "entity_labels"):
        e = c["tables"][table]
        cls = ns[e["class"]]
        g.add((cls, RDF.type, OWL.Class))
        g.add((cls, RDFS.label, Literal(e["label"], lang="en")))
        g.add((cls, RDFS.comment, Literal(e["definition"])))
        for col, f in e["fields"].items():
            prop = ns[table + "_" + col]
            g.add((prop, RDF.type, OWL.DatatypeProperty))
            g.add((prop, RDFS.domain, cls))
            g.add((prop, RDFS.label, Literal(col)))
            g.add((prop, RDFS.comment, Literal(f["definition"])))
    prop = ns.observedInEvent
    g.add((prop, RDF.type, OWL.ObjectProperty))
    g.add((prop, RDFS.domain, ns.RankingObservation))
    g.add((prop, RDFS.range, ns.Event))
    g.add((prop, RDFS.comment, Literal(relation["definition"])))
    g.serialize(ARTIFACTS / "ontology.ttl", format="turtle")
    print(
        "Published",
        c["version"],
        "with",
        m["row_count"],
        "ranking observations and",
        len(shots),
        "NL2SQL examples.",
    )


if __name__ == "__main__":
    run()
