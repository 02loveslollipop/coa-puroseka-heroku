"""Ensure the daily index, then validate and publish curated SQL examples.

Operator-only: no model calls, dataset generation, migrations or new resources.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlglot import parse_one, exp
from shared.config import settings
from shared.database import schema_name
from services.ontology.query import execute_query

ROOT = Path(__file__).resolve().parents[1]


def update():
    cfg = dotenv_values(ROOT / ".env")
    settings().update(
        DATABASE_URL=cfg["MIGRATION_DATABASE_URL"],
        DATA_SCHEMA=cfg.get("DATA_SCHEMA", "coa_puroseka"),
    )
    schema = schema_name()
    with psycopg.connect(
        cfg.get("MIGRATION_ADMIN_DATABASE_URL") or cfg["MIGRATION_DATABASE_URL"],
        autocommit=True,
    ) as conn:
        conn.execute("SET statement_timeout='180s'")
        ddl = (ROOT / "services/ontology/daily-index.sql").read_text()
        conn.execute(
            ddl.replace("coa_puroseka.", sql.Identifier(schema).as_string(conn) + ".")
        )
    with psycopg.connect(cfg["MIGRATION_DATABASE_URL"]) as conn:
        catalog = conn.execute(
            sql.SQL(
                "SELECT payload FROM {}.ontology_versions WHERE name='catalog.json'"
            ).format(sql.Identifier(schema))
        ).fetchone()[0]
    examples = json.loads(
        (ROOT / "services/ontology/semantic/few_shots.json").read_text()
    )
    for example in examples:
        tree = parse_one(example["sql"], read="postgres")
        for table in tree.find_all(exp.Table):
            if table.db == "data":
                table.set("db", exp.Identifier(this=schema))
        result = execute_query(tree.sql(dialect="postgres"), set(catalog["tables"]))
        example["sql"] = result["sql"]
    with psycopg.connect(
        cfg.get("MIGRATION_ADMIN_DATABASE_URL") or cfg["MIGRATION_DATABASE_URL"]
    ) as conn:
        conn.execute(
            sql.SQL(
                "UPDATE {}.ontology_versions SET payload=%s WHERE name='few_shots.json'"
            ).format(sql.Identifier(schema)),
            (Jsonb(examples),),
        )
        for number, example in enumerate(examples):
            conn.execute(
                sql.SQL(
                    "INSERT INTO {}.ontology_examples(id,payload) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload"
                ).format(sql.Identifier(schema)),
                (number, Jsonb(example)),
            )
    print(json.dumps({"examples_validated_and_published": len(examples)}))


if __name__ == "__main__":
    update()
