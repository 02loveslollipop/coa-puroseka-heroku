"""One-time deterministic source profiling, normalization and semantic artifact build."""

import argparse
import collections
import gzip
import hashlib
import json
import pathlib
import re
from rdflib import Graph, Namespace, RDF, RDFS, OWL, Literal

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = {
    "characters": "chracters.json",
    "events": "events.json",
    "cards": "cards.json",
    "event_cards": "eventCards.json",
    "gachas": "gachas.json",
}
COLLECTIONS = {
    "gachaDetails": "gacha_details",
    "gachaPickups": "gacha_pickups",
    "gachaBehaviors": "gacha_behaviors",
    "gachaCardRarityRates": "gacha_rarity_rates",
    "cardParameters": "card_parameters",
    "specialTrainingCosts": "card_training_costs",
    "masterLessonAchieveResources": "card_master_resources",
    "eventRankingRewardRanges": "event_reward_ranges",
    "eventRankingRewards": "event_rewards",
}


def snake(k):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower()


def dtype(values):
    values = [v for v in values if v is not None]
    if not values:
        return "text"
    if all(isinstance(v, bool) for v in values):
        return "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "bigint"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "double precision"
    if any(isinstance(v, (dict, list)) for v in values):
        return "jsonb"
    return "text"


def build(source, out):
    out.mkdir(parents=True, exist_ok=True)
    tables = collections.defaultdict(list)
    lineage = {}
    sources = []

    def add(table, record, path, parent=None):
        row = {
            snake(k): v for k, v in record.items() if not isinstance(v, (dict, list))
        }
        if parent is not None:
            row["parent_id"] = parent
        row["row_key"] = path
        row["raw_record"] = record
        tables[table].append(row)
        lineage.setdefault(table, [])
        for k, v in record.items():
            if k in COLLECTIONS:
                child = COLLECTIONS[k]
                lineage.setdefault(child, [])
                if isinstance(v, list):
                    for idx, item in enumerate(v):
                        if isinstance(item, dict):
                            add(
                                child,
                                item,
                                f"{path}/{k}/{idx}",
                                record["id"] if table in FILES else path,
                            )

    for table, name in FILES.items():
        f = source / name
        blob = f.read_bytes()
        rows = json.loads(blob)
        sources.append(
            {
                "file": name,
                "sha256": hashlib.sha256(blob).hexdigest(),
                "records": len(rows),
                "bytes": len(blob),
            }
        )
        for index, r in enumerate(rows):
            add(table, r, f"{name}/{index}")
        lineage[table] = [name]
    definitions = json.loads((ROOT / "semantic/definitions.json").read_text())
    catalog = {
        "version": definitions["version"],
        "description": definitions["description"],
        "tables": {},
        "relationships": [],
        "rules": definitions["rules"],
    }
    schema = {}
    for t, rows in tables.items():
        keys = sorted(set().union(*(r.keys() for r in rows)))
        cols = {k: dtype([r.get(k) for r in rows]) for k in keys}
        schema[t] = cols
        entity = definitions["entities"].get(t)
        if not entity:
            cls, desc = definitions["collections"][t]
            entity = {
                "class": cls,
                "label": re.sub(r"(?<!^)(?=[A-Z])", " ", cls),
                "definition": desc,
                "fields": {},
            }
        fields = {
            k: {
                "type": typ,
                "definition": entity.get("fields", {}).get(
                    k,
                    "Source row preserved as JSON, including nested content."
                    if k == "raw_record"
                    else "Import provenance path; uniquely identifies this row."
                    if k == "row_key"
                    else "Identifier of the containing source entity."
                    if k == "parent_id"
                    else f"Source field {k}; no additional domain interpretation asserted.",
                ),
            }
            for k, typ in cols.items()
        }
        catalog["tables"][t] = {
            **entity,
            "fields": fields,
            "row_count": len(rows),
            "source_files": sorted(set(r["row_key"].split("/")[0] for r in rows)),
        }
    for src, col, dst, dcol, name, desc in definitions["relationships"]:
        if src not in tables or col not in schema[src]:
            continue
        targets = {r.get(dcol) for r in tables[dst]}
        bad = collections.Counter(
            str(r[col])
            for r in tables[src]
            if r.get(col) is not None and r[col] not in targets
        )
        catalog["relationships"].append(
            {
                "source": src,
                "column": col,
                "target": dst,
                "target_column": dcol,
                "name": name,
                "definition": desc,
                "unresolved_count": sum(bad.values()),
                "unresolved_values": dict(bad),
                "status": "validated" if not bad else "partial",
            }
        )
    payload = {"schema": schema, "tables": dict(tables)}
    with gzip.open(out / "dataset.json.gz", "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    (out / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
    manifest = {
        "version": catalog["version"],
        "sources": sources,
        "row_counts": {t: len(v) for t, v in tables.items()},
        "relationships": catalog["relationships"],
        "dataset_sha256": hashlib.sha256(
            (out / "dataset.json.gz").read_bytes()
        ).hexdigest(),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    g = Graph()
    ns = Namespace(definitions["namespace"])
    g.bind("ps", ns)
    g.add((ns.Ontology, RDF.type, OWL.Ontology))
    g.add((ns.Ontology, OWL.versionInfo, Literal(catalog["version"])))
    for t, e in catalog["tables"].items():
        cls = ns[e["class"]]
        g.add((cls, RDF.type, OWL.Class))
        g.add((cls, RDFS.label, Literal(e["label"], lang="en")))
        g.add((cls, RDFS.comment, Literal(e["definition"], lang="en")))
        for k, f in e["fields"].items():
            prop = ns[t + "_" + k]
            g.add((prop, RDF.type, OWL.DatatypeProperty))
            g.add((prop, RDFS.domain, cls))
            g.add((prop, RDFS.label, Literal(k)))
            g.add((prop, RDFS.comment, Literal(f["definition"])))
    for r in catalog["relationships"]:
        prop = ns[r["name"]]
        g.add((prop, RDF.type, OWL.ObjectProperty))
        g.add((prop, RDFS.domain, ns[catalog["tables"][r["source"]]["class"]]))
        g.add((prop, RDFS.range, ns[catalog["tables"][r["target"]]["class"]]))
        g.add((prop, RDFS.comment, Literal(r["definition"])))
    g.serialize(out / "ontology.ttl", format="turtle")
    print(
        json.dumps(
            {
                "row_counts": manifest["row_counts"],
                "unresolved_relationships": [
                    r for r in catalog["relationships"] if r["unresolved_count"]
                ],
                "ontology_triples": len(g),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--source",
        type=pathlib.Path,
        default=pathlib.Path(
            "/home/zerotwo/Documents/puroseka/puroseka/raw_datasets/metadata"
        ),
    )
    p.add_argument("--out", type=pathlib.Path, default=ROOT / "artifacts")
    a = p.parse_args()
    build(a.source, a.out)
