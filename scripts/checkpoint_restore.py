#!/usr/bin/env python3
"""Check retention of real RocksDB backups in an owned staging directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import check_native_checkpoints as checkpoints
from rocksdb_lifecycle import CORPUS, ROOT, SST

READER = ROOT / "scripts/rocksdb_checkpoint_reader.cc"
RECEIPT = ROOT / "results/rocksdb-lifecycle/checkpoint-restore.json"
VIEWS = ("before", "after")
POLICIES = ("keep-before", "keep-after", "keep-both")


def binding() -> dict:
    return {**checkpoints.source_binding(), **{
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (Path(__file__).resolve(), READER, checkpoints.RECEIPT)
    }}


def run_case(snapshots: Path, work: Path, reader: Path, views: dict, chosen: str, policy: str) -> dict:
    # The selection is a controlled outcome. The retention decision uses
    # only the two candidates and their immutable file identities.
    required = set(views[chosen])
    candidates = set().union(*(views[name] for name in VIEWS))
    kept = candidates if policy == "keep-both" else set(views[policy.removeprefix("keep-")])
    pool, destination = work / "pool", work / "restore"
    pool.mkdir()
    destination.mkdir()
    for name in sorted(candidates):
        src = next(snapshots / view / name for view in VIEWS if name in views[view])
        if not SST.fullmatch(name) or not src.is_file():
            raise ValueError("invalid native source table")
        shutil.copyfile(src, pool / name)
    for name in sorted(candidates - kept):
        (pool / name).unlink()
    for path in (snapshots / chosen).iterdir():
        if not path.is_file() or path.is_symlink():
            raise ValueError("unexpected checkpoint member")
        if not SST.fullmatch(path.name):
            shutil.copyfile(path, destination / path.name)
    for name in sorted(required & kept):
        shutil.copyfile(pool / name, destination / name)
    missing = sorted(name for name in required if not (destination / name).is_file())
    status = subprocess.check_output([str(reader), str(destination), *map(str, CORPUS[:2])],
                                     text=True, timeout=30).strip()
    if not re.fullmatch(r"ok:64|(?:open|read):[1-9][0-9]*", status):
        raise ValueError(f"unexpected reader output: {status}")
    return dict(chosen=chosen, policy=policy, reclaimed=sorted(candidates - kept),
                missing=missing, native_status=status)


def evaluate(rows: list[dict], views: dict) -> dict:
    matrix = [(v, p) for v in VIEWS for p in POLICIES]
    if [(r["chosen"], r["policy"]) for r in rows] != matrix:
        raise ValueError("incomplete checkpoint-restore comparison")
    counts = {p: 0 for p in POLICIES}
    for row in rows:
        chosen, policy = row["chosen"], row["policy"]
        missing = set(views[chosen]) & set(row["reclaimed"])
        expected_removed = set() if policy == "keep-both" else (
            set().union(*(views[name] for name in VIEWS)) - set(views[policy.removeprefix("keep-")]))
        if row["reclaimed"] != sorted(expected_removed) or row["missing"] != sorted(missing):
            raise ValueError("backup inventory does not match the observed missing tables")
        # The native failure kind is not used to derive the file inventory.
        # Status codes are normalized because releases can classify absence differently.
        status = row["native_status"]
        if status not in ("open-failed", "read-failed", "ok:64") or (status == "ok:64") == bool(missing):
            raise ValueError("native restore did not agree with the matched control")
        counts[policy] += status != "ok:64"
    return {"trials": len(rows), "failures": counts,
            "exact_reopens": len(rows) - sum(counts.values()), "keys_per_reopen": 64}


def reference() -> dict:
    value = json.loads(checkpoints.RECEIPT.read_bytes())
    checkpoints.checked(value)
    return value["native"]


def capture() -> dict:
    sources = binding()
    expected = reference()
    with tempfile.TemporaryDirectory(prefix="rf-native-checkpoint-restore-") as td:
        work = Path(td)
        native, reader, snapshots = work / "observer", work / "reader", work / "snapshots"
        snapshots.mkdir()
        for source, binary in ((checkpoints.SOURCE, native), (READER, reader)):
            subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-O2", "-Wall",
                            "-Wextra", str(source), "-lrocksdb", "-o", str(binary)],
                           check=True, timeout=120)
        captured = json.loads(subprocess.check_output(
            [str(native), str(snapshots), *map(str, CORPUS[:2])], text=True, timeout=120))
        # File identities and the logical sequence must match. File sizes
        # and the reported library version belong to the separate native receipt.
        if (captured["sequence"], captured["verified_keys"]) != (expected["sequence"], expected["verified_keys"]) or any(
                set(captured[v]) != set(expected[v]) for v in VIEWS):
            raise ValueError("fresh checkpoint fixture does not match the retained file identities")
        rows = []
        for chosen in VIEWS:
            for policy in POLICIES:
                trial = work / f"{chosen}-{policy}"
                trial.mkdir()
                row = run_case(snapshots, trial, reader, captured, chosen, policy)
                if row["native_status"] != "ok:64":
                    row["native_status"] = row["native_status"].split(":")[0] + "-failed"
                rows.append(row)
    if sources != binding():
        raise ValueError("inputs changed during native restore comparison")
    return dict(sources=sources, rows=rows, summary=evaluate(rows, expected))


def checked(value: dict) -> dict:
    if value["sources"] != binding() or value["summary"] != evaluate(value["rows"], reference()):
        raise ValueError("checkpoint restore evidence is stale")
    return value["summary"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--record", type=Path)
    group.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.record:
        value = capture()
        with args.record.open("x", encoding="utf-8", newline="\n") as f:
            json.dump(value, f, indent=2, sort_keys=True)
            f.write("\n")
    else:
        value = json.loads(RECEIPT.read_bytes())
        if args.run and capture() != value:
            raise ValueError("checkpoint restore replay differs from the receipt")
    print(json.dumps(checked(value), sort_keys=True))


if __name__ == "__main__":
    main()
