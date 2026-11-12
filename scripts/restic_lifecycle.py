#!/usr/bin/env python3
"""Observe snapshot retirement and pack relocation in an owned restic repository."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile

from rocksdb_lifecycle import CORPUS, ROOT

RECEIPT = ROOT / "results/restic-lifecycle/observations.json"
STAGES = ("both-snapshots", "after-forget", "after-prune")


def summarize(rows: list[dict]) -> dict:
    if [row["stage"] for row in rows] != list(STAGES):
        raise ValueError("missing lifecycle stage")
    before, forgotten, after = rows
    if set(before["snapshots"]) != {"first", "second"} or any(
        set(row["snapshots"]) != {"second"} for row in rows[1:]
    ):
        raise ValueError("unexpected live snapshot set")
    for row in rows:
        if len(set(row["packs"])) != len(row["packs"]):
            raise ValueError("duplicate pack identifiers")
        if set(row["snapshots"]) != set(row["restores"]):
            raise ValueError("not all retained snapshots were restored")
        for blobs in row["snapshots"].values():
            if any(not packs or not set(packs) <= set(row["packs"]) for packs in blobs.values()):
                raise ValueError("snapshot has a missing pack location")
    old, new = before["snapshots"]["first"], before["snapshots"]["second"]
    if set(new) != set(forgotten["snapshots"]["second"]) or set(new) != set(after["snapshots"]["second"]):
        raise ValueError("surviving snapshot references changed")
    if len(set(old) & set(new)) != 1:
        raise ValueError("expected one shared data blob across snapshots")
    if before["packs"] != forgotten["packs"]:
        raise ValueError("snapshot removal unexpectedly changed the pack inventory")
    surviving = after["snapshots"]["second"]
    removed = sorted(set(before["packs"]) - set(after["packs"]))
    relocated = sorted(key for key in new if set(new[key]).isdisjoint(surviving[key]))
    if not removed or not relocated:
        raise ValueError("expected a removed pack and a relocated live blob")
    return {
        "shared_data_blobs": len(set(old) & set(new)),
        "snapshots_restored": sum(len(row["restores"]) for row in rows),
        "packs_before": len(before["packs"]),
        "packs_after": len(after["packs"]),
        "packs_removed": len(removed),
        "surviving_data_blobs_relocated": len(relocated),
    }


def run() -> dict:
    sources = source_binding()
    source = {p.name.split("-", 2)[-1]: p.read_bytes() for p in CORPUS}
    first = {k: source[k] for k in ("hash.h", "mutex.h")}
    second = {k: source[k] for k in ("hash.h", "pcache.h")}
    with tempfile.TemporaryDirectory(prefix="retirement-restic-") as tmp:
        root = Path(tmp)
        folder = root / "source"
        folder.mkdir()
        password = root / "password"
        password.write_text(secrets.token_urlsafe(30), encoding="ascii")
        os.chmod(password, 0o600)
        env = {k: v for k, v in os.environ.items() if not k.startswith("RESTIC_")}
        env["GOMAXPROCS"] = "4"

        def cli(*cmd: str) -> str:
            result = subprocess.run(
                ["restic", "--repo", str(root / "repo"), "--password-file", str(password),
                 "--no-cache", *cmd], cwd=folder, env=env, capture_output=True,
                text=True, timeout=90,
            )
            if result.returncode:
                raise RuntimeError(f"restic {cmd[0]} failed: {result.stderr.strip()}")
            return result.stdout

        def write(files: dict[str, bytes]) -> str:
            for path in folder.iterdir():
                path.unlink()
            for name, content in files.items():
                (folder / name).write_bytes(content)
            summary, = (item for item in map(json.loads, cli("backup", "--json", ".").splitlines())
                        if item["message_type"] == "summary")
            return summary["snapshot_id"]

        def observe(stage: str, targets: dict[str, tuple[str, dict[str, bytes]]]) -> dict:
            packs = sorted(cli("list", "packs").split())
            locations: dict[str, set[str]] = {}
            for index in cli("list", "index").split():
                for pack in json.loads(cli("cat", "index", index))["packs"]:
                    for blob in pack["blobs"]:
                        if blob["type"] == "data":
                            locations.setdefault(blob["id"], set()).add(pack["id"])
            snapshots, restores = {}, {}
            for tag, (identifier, expected) in targets.items():
                nodes = json.loads(cli("cat", "tree", identifier))["nodes"]
                expected_ids = {hashlib.sha256(value).hexdigest() for value in expected.values()}
                blob_ids = {blob for node in nodes for blob in (node.get("content") or [])}
                if blob_ids != expected_ids:
                    raise ValueError("snapshot tree does not match the fixture byte identities")
                snapshots[tag] = {key: sorted(locations[key]) for key in sorted(blob_ids)}
                restored = root / f"restore-{stage}-{tag}"
                cli("restore", identifier, "--target", str(restored))
                actual = [(p.name, p.read_bytes()) for p in restored.rglob("*") if p.is_file()]
                if sorted(actual) != sorted(expected.items()):
                    raise ValueError("native restored files differ from input bytes")
                restores[tag] = sorted(expected)
            return {"stage": stage, "packs": packs, "snapshots": snapshots, "restores": restores}

        cli("init")
        old_id, new_id = write(first), write(second)
        rows = [observe(STAGES[0], {"first": (old_id, first), "second": (new_id, second)})]
        cli("forget", old_id)
        rows.append(observe(STAGES[1], {"second": (new_id, second)}))
        cli("prune", "--max-unused", "0")
        cli("check", "--read-data")
        rows.append(observe(STAGES[2], {"second": (new_id, second)}))
        version = cli("version").split()[1]
    if sources != source_binding():
        raise ValueError("restic observer inputs changed during execution")
    return {"restic": version, "observations": rows, "summary": summarize(rows),
            "sources": sources}


def source_binding() -> dict[str, str]:
    files = (Path(__file__).resolve(), Path(__file__).with_name("rocksdb_lifecycle.py").resolve(), *CORPUS)
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def check(value: dict) -> dict:
    if re.fullmatch(r"\d+\.\d+\.\d+", value["restic"]) is None:
        raise ValueError("missing restic version")
    if value["sources"] != source_binding():
        raise ValueError("restic observation source or public input changed")
    totals = summarize(value["observations"])
    if totals != value["summary"]:
        raise ValueError("summary is not supported by the retained observations")
    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    if args.record and not args.run:
        parser.error("--record requires --run")
    value = run() if args.record else json.loads(RECEIPT.read_text(encoding="utf-8"))
    summary = check(value)
    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        with args.record.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
    elif args.run:
        other = run()
        # Pack IDs depend on repository encryption randomness; compare outcomes.
        if (other["restic"], check(other)) != (value["restic"], summary):
            raise ValueError("native rerun differs from the retained outcome")
    print(json.dumps(summary, sort_keys=True))
