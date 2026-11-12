#!/usr/bin/env python3
"""Record native RocksDB lifecycle observations and check their paper consumer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "results/rocksdb-lifecycle/observations.json"
SOURCE = ROOT / "scripts/rocksdb_lifecycle.cc"
CORPUS = tuple(
    ROOT / "inputs/sqlite" / f"version-3.47.0-{stem}.h"
    for stem in ("hash", "mutex", "pcache")
)
WORKLOADS = (("full", 8), ("full", 24), ("full", 64), ("range", 32))
STAGES = ("before-live", "before-reopen", "before-compaction", "after-live", "after-reopen")
SST = re.compile(r"[0-9]+\.sst")


def source_binding() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__).resolve(), SOURCE, *CORPUS)
    }


def checked_case(scope: str, batch_size: int, observations: list[dict]) -> dict:
    if [row["stage"] for row in observations] != list(STAGES):
        raise ValueError("missing or out-of-order native observations")
    sizes: dict[str, int] = {}
    for row in observations:
        keys = 3 * batch_size if scope == "range" else 5 * batch_size // 2
        if row["sequence"] != 4 * batch_size or row["verified_keys"] != keys:
            raise ValueError("compaction changed the logical workload or coverage")
        if not re.fullmatch(r"MANIFEST-\d+", row["manifest"]) or row["manifest_bytes"] <= 0:
            raise ValueError("missing native manifest observation")
        for field in ("live", "disk"):
            for name, size in row[field].items():
                if SST.fullmatch(name) is None or type(size) is not int or size <= 0:
                    raise ValueError("invalid native SST observation")
                if sizes.setdefault(name, size) != size:
                    raise ValueError("immutable SST changed size")
        if not row["live"] or not row["live"].items() <= row["disk"].items():
            raise ValueError("native view refers to an unavailable table")
    before, after = observations[1]["live"], observations[4]["live"]
    if not before == observations[0]["live"] == observations[2]["live"] or after != observations[3]["live"]:
        raise ValueError("native reopen disagreed with the live file set")
    manifest_before, manifest_after = observations[2:4]
    if (manifest_before["manifest"] != manifest_after["manifest"]
            or manifest_after["manifest_bytes"] <= manifest_before["manifest_bytes"]):
        raise ValueError("expected a compaction-time edit in the same manifest")
    old_only = sorted(before.keys() - after.keys())
    new_only = sorted(after.keys() - before.keys())
    shared = sorted(before.keys() & after.keys())
    if not old_only or not new_only or len(shared) != (1 if scope == "range" else 0):
        raise ValueError("the workload did not replace physical tables")
    return {
        "scope": scope,
        "batch_size": batch_size,
        "observations": observations,
        # An offline calculation on supplied views, not observed crash outcomes.
        "two_view_projection": {
            "required": sorted(before.keys() | after.keys()),
            "shared": shared,
            "missing_if_keep_before_only": new_only,
            "missing_if_keep_after_only": old_only,
            "old_only_removed_by_native_engine": [
                name for name in old_only if name not in observations[4]["disk"]
            ],
        },
    }


def checked_receipt(receipt: dict) -> list[dict]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", receipt["rocksdb"]):
        raise ValueError("missing RocksDB build version")
    if receipt["sources"] != source_binding():
        raise ValueError("native observation source or public input changed")
    if [(case["scope"], case["batch_size"]) for case in receipt["cases"]] != list(WORKLOADS):
        raise ValueError("incomplete prescribed workload set")
    cases = [checked_case(case["scope"], case["batch_size"], case["observations"])
             for case in receipt["cases"]]
    if cases != receipt["cases"]:
        raise ValueError("stored projection differs from native rows")
    return cases


def paper_table(receipt: dict) -> str:
    cases = checked_receipt(receipt)
    lines = ["% Generated from the retained RocksDB observations."]
    lines.append(r"\newcommand{\RocksNativeCases}{" + str(len(cases)) + "}")
    lines.append(r"\newcommand{\RocksNativeOpens}{" + str(2 * len(cases)) + "}")
    lines.append(r"\newcommand{\RocksNativeVersion}{" + receipt["rocksdb"] + "}")
    lines.append(r"\newcommand{\RocksNativeRows}{%")
    for case in cases:
        before, after = case["observations"][1], case["observations"][4]
        projection = case["two_view_projection"]
        numbers = (
            case["scope"].title(), before["verified_keys"], before["sequence"],
            len(before["live"]), len(after["live"]), len(projection["shared"]),
            len(projection["missing_if_keep_before_only"]),
            len(projection["missing_if_keep_after_only"]),
            len(projection["old_only_removed_by_native_engine"]),
        )
        lines.append(" & ".join(map(str, numbers)) + r" \\")
    return "\n".join(lines + ["}", ""])


def execute() -> dict:
    sources = source_binding()
    with tempfile.TemporaryDirectory(prefix="retirement-rocksdb-") as work:
        workspace = Path(work)
        binary = workspace / "observe"
        subprocess.run(
            [os.environ.get("CXX", "c++"), "-std=c++17", "-O2", "-Wall", "-Wextra",
             str(SOURCE), "-lrocksdb", "-o", str(binary)],
            check=True, timeout=180,
        )
        version = None
        cases = []
        for scope, count in WORKLOADS:
            run = subprocess.run(
                [str(binary), str(workspace / f"database-{count}"), str(count), scope,
                 *map(str, CORPUS)],
                capture_output=True, text=True, timeout=120,
            )
            if run.returncode:
                raise RuntimeError(run.stderr.strip() or "native observer failed")
            metadata, *rows = map(json.loads, run.stdout.splitlines())
            if version is not None and metadata["rocksdb"] != version:
                raise ValueError("RocksDB build changed between cases")
            version = metadata["rocksdb"]
            cases.append(checked_case(scope, count, rows))
    if sources != source_binding():
        raise ValueError("native observer inputs changed during execution")
    return {"rocksdb": version, "cases": cases, "sources": sources}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="rerun on fresh native databases")
    parser.add_argument("--record", type=Path, help="record a new run at a specified path")
    parser.add_argument("--table", type=Path, help="check the corresponding TeX table")
    parser.add_argument("--emit-table", type=Path, help="generate the TeX table for reviewed observations")
    args = parser.parse_args()
    if args.record and not args.run:
        parser.error("--record requires --run")
    try:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8")) if not args.record else None
        if args.run:
            new = execute()
            checked_receipt(new)
            if args.record:
                args.record.parent.mkdir(parents=True, exist_ok=True)
                with args.record.open("x", encoding="utf-8", newline="\n") as out:
                    json.dump(new, out, indent=2, sort_keys=True)
                    out.write("\n")
                receipt = new
            elif new != receipt:
                raise ValueError("native rerun differs from retained observations")
        assert receipt is not None
        rendered = paper_table(receipt)
        if args.table and args.table.read_text(encoding="utf-8") != rendered:
            raise ValueError("paper table is stale")
        if args.emit_table:
            with args.emit_table.open("x", encoding="utf-8", newline="\n") as out:
                out.write(rendered)
        print(json.dumps({"rocksdb": receipt["rocksdb"], "cases": len(checked_receipt(receipt))}))
        return 0
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"RocksDB lifecycle: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
