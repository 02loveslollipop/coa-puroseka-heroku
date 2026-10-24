"""Operator-only resumable preparation. Never included as a web API or agent."""

import argparse
import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import artifact, ARTIFACTS, settings
from app.llm import complete


async def run(check_only=False):
    content, usage = await complete(
        [{"role": "user", "content": "Reply with the single word ready."}]
    )
    print(
        "Mistral completion verified; model:",
        settings().get("MISTRAL_MODEL", "ministral-8b-latest"),
        "tokens:",
        usage.get("total_tokens"),
        flush=True,
    )
    if check_only:
        return
    import gzip

    with gzip.open(ARTIFACTS / "dataset.json.gz", "rt") as f:
        tables = json.load(f)["tables"]
    records = []
    for table, field in [("events", "name"), ("cards", "prefix"), ("gachas", "name")]:
        records.extend(
            {"table": table, "id": r["id"], "original": r[field]}
            for r in tables[table]
            if r.get(field)
        )
    target = ARTIFACTS / "translations.json"
    saved = (
        json.loads(target.read_text())
        if target.exists()
        else {
            "version": artifact("catalog.json")["version"],
            "model": settings().get("MISTRAL_MODEL", "ministral-8b-latest"),
            "labels": [],
        }
    )
    cache = {r["original"]: r["english"] for r in saved["labels"] if r.get("english")}
    originals = list(
        dict.fromkeys(r["original"] for r in records if r["original"] not in cache)
    )
    for start in range(0, len(originals), 12):
        batch = originals[start : start + 12]
        prompt = (
            "Translate these Japanese Project SEKAI card/event/gacha titles into concise English convenience labels. These are NOT official localization. Preserve punctuation/names where appropriate. Return a JSON object with translations, an array of strings in exactly the same order. Do not omit titles. INPUT: "
            + json.dumps(batch, ensure_ascii=False)
        )
        raw, _ = await complete([{"role": "user", "content": prompt}], True)
        try:
            values = json.loads(raw)["translations"]
            assert len(values) == len(batch) and all(
                isinstance(v, str) and v.strip() for v in values
            )
        except Exception as e:
            raise RuntimeError(
                "Invalid translation batch; completed batches are saved. Run again to resume."
            ) from e
        cache.update(zip(batch, values))
        saved["labels"] = [
            {
                **r,
                "english": cache[r["original"]],
                "provenance": "generated_mistral_approximate",
                "model": saved["model"],
            }
            for r in records
            if r["original"] in cache
        ]
        for c in tables["characters"]:
            saved["labels"].append(
                {
                    "table": "characters",
                    "id": c["id"],
                    "original": str(c.get("first_name", ""))
                    + str(c.get("given_name", "")),
                    "english": (
                        str(c.get("first_name_english", ""))
                        + " "
                        + str(c.get("given_name_english", ""))
                    ).strip(),
                    "provenance": "source_english_fields",
                }
            )
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(saved, ensure_ascii=False, indent=2))
        temp.replace(target)
        print(
            f"Translated {min(start + 12, len(originals))}/{len(originals)} unique titles; saved {len(saved['labels'])} labels",
            flush=True,
        )
    catalog = artifact("catalog.json")
    catalog["tables"]["entity_labels"] = {
        "class": "EntityLabel",
        "label": "Entity label",
        "definition": "A convenience English label for a source entity. Generated labels are approximate translations; character English names come from source fields. Join entity_id to the table named by entity_table.",
        "aliases": ["English title", "translation", "English name"],
        "row_count": len(saved["labels"]),
        "fields": {
            k: {"type": t, "definition": d}
            for k, t, d in [
                ("row_key", "text", "Unique entity table / ID path."),
                (
                    "entity_table",
                    "text",
                    "Target source table name: events, cards, gachas, or characters.",
                ),
                (
                    "entity_id",
                    "bigint",
                    "Target source entity ID; only meaningful together with entity_table.",
                ),
                ("original", "text", "Original Japanese source label."),
                (
                    "english",
                    "text",
                    "Approximate generated English label, or source-supplied character English name.",
                ),
                (
                    "provenance",
                    "text",
                    "source_english_fields or generated_mistral_approximate.",
                ),
                (
                    "raw_record",
                    "jsonb",
                    "Label record including translation model provenance.",
                ),
            ]
        },
    }
    (ARTIFACTS / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2)
    )
    # Add label semantics to the exported ontology without inventing a fixed-range polymorphic edge.
    from rdflib import Graph, Namespace, RDF, RDFS, OWL, Literal

    g = Graph().parse(ARTIFACTS / "ontology.ttl")
    ns = Namespace("https://puroseka.example/ontology/")
    g.add((ns.EntityLabel, RDF.type, OWL.Class))
    g.add((ns.EntityLabel, RDFS.label, Literal("Entity label", lang="en")))
    g.add(
        (
            ns.EntityLabel,
            RDFS.comment,
            Literal(catalog["tables"]["entity_labels"]["definition"]),
        )
    )
    g.serialize(ARTIFACTS / "ontology.ttl", format="turtle")
    manifest = artifact("manifest.json")
    manifest["row_counts"]["entity_labels"] = len(saved["labels"])
    manifest["translation_model"] = saved["model"]
    manifest["translation_count"] = sum(
        r["provenance"] == "generated_mistral_approximate" for r in saved["labels"]
    )
    (ARTIFACTS / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print("One-time enrichment complete.", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--check-only", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.check_only))
