"""Stream all source ranking files into a compressed JSONL import artifact."""

import argparse
import collections
import gzip
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(source, out):
    files = sorted(
        source.glob("*/*.json"),
        key=lambda p: (int(p.parent.name), int(p.stem.split("_")[-1])),
    )
    counts = collections.Counter()
    ranks = collections.Counter()
    earliest = None
    latest = None
    total = 0
    source_manifest = []
    errors = []
    mismatches = 0
    seen_timestamps = set()
    duplicate_timestamps = 0
    null_entries = 0
    with gzip.open(out / "rankings.jsonl.gz", "wt", encoding="utf-8") as target:
        for i, f in enumerate(files):
            blob = f.read_bytes()
            payload = json.loads(blob)
            data = payload.get("data", {})
            rows = (
                (data.get("eventRankings", []) or []) if isinstance(data, dict) else []
            )
            if payload.get("status") != "success":
                errors.append(
                    {
                        "file": str(f.relative_to(source)),
                        "status": payload.get("status"),
                    }
                )
            path = str(f.relative_to(source))
            seen_timestamps.clear()
            for index, r in enumerate(rows):
                if r is None:
                    null_entries += 1
                    continue
                if not isinstance(r, dict):
                    raise ValueError(f"Unexpected ranking entry: {path}/{index}")
                stamp = r.get("timestamp")
                if not stamp or not all(k in r for k in ("eventId", "rank", "score")):
                    raise ValueError(f"Malformed ranking row: {path}/{index}")
                if int(r["eventId"]) != int(f.parent.name) or int(r["rank"]) != int(
                    f.stem.split("_")[-1]
                ):
                    mismatches += 1
                if stamp in seen_timestamps:
                    duplicate_timestamps += 1
                seen_timestamps.add(stamp)
                row = {
                    "row_key": path + "/" + str(index),
                    "event_id": int(r["eventId"]),
                    "rank": int(r["rank"]),
                    "score": int(r["score"]),
                    "observed_at": stamp,
                    "user_id": str(r["userId"])
                    if r.get("userId") is not None
                    else None,
                    "leader_card_id": r.get("userCard", {}).get("cardId")
                    if isinstance(r.get("userCard"), dict)
                    else None,
                    "source_file": path,
                    "raw_record": r,
                }
                target.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                total += 1
                counts[str(r["eventId"])] += 1
                ranks[str(r["rank"])] += 1
                earliest = stamp if earliest is None else min(earliest, stamp)
                latest = stamp if latest is None else max(latest, stamp)
            source_manifest.append(
                {
                    "file": path,
                    "rows": len(rows),
                    "bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                }
            )
            if (i + 1) % 100 == 0:
                print(
                    f"Prepared {i + 1}/{len(files)} ranking files; {total:,} observations",
                    flush=True,
                )
    manifest = {
        "source_directory_count": len([d for d in source.iterdir() if d.is_dir()]),
        "directories_with_files": len({f.parent.name for f in files}),
        "empty_directories": sorted(
            [
                d.name
                for d in source.iterdir()
                if d.is_dir() and not list(d.glob("*.json"))
            ],
            key=int,
        ),
        "source_file_count": len(files),
        "row_count": total,
        "events": len(counts),
        "rows_per_event": dict(counts),
        "rows_per_rank": dict(ranks),
        "earliest_observed_at": earliest,
        "latest_observed_at": latest,
        "file_field_mismatches": mismatches,
        "duplicate_series_timestamps": duplicate_timestamps,
        "null_entries_skipped": null_entries,
        "non_success_files": errors,
        "sources": source_manifest,
    }
    (out / "ranking_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in manifest.items()
                if k not in ("sources", "rows_per_event", "rows_per_rank")
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--source",
        type=pathlib.Path,
        default=pathlib.Path(
            "/home/zerotwo/Documents/puroseka/puroseka/raw_datasets/event_rank_snapshots"
        ),
    )
    p.add_argument("--out", type=pathlib.Path, default=ROOT / "artifacts")
    a = p.parse_args()
    run(a.source, a.out)
