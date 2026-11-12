#!/usr/bin/env python3
"""Validate two live native checkpoints after disposing of an older backup."""
from __future__ import annotations

import argparse
import hashlib
from itertools import combinations
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from check_native_epochs import required_while_unresolved
from rocksdb_lifecycle import CORPUS, ROOT, SST

SOURCE = ROOT / "scripts/rocksdb_checkpoint_probe.cc"
RECEIPT = ROOT / "results/rocksdb-lifecycle/checkpoints.json"
VIEWS = ("archive", "before", "after")


def source_binding() -> dict[str, str]:
    files = (Path(__file__).resolve(), SOURCE, ROOT / "scripts/check_native_epochs.py",
             ROOT / "scripts/rocksdb_lifecycle.py", *CORPUS[:2])
    return {file.relative_to(ROOT).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in files}


def evaluate(native: dict) -> dict:
    if set(native) != {"rocksdb", "sequence", "verified_keys", "archived_unlinked",
                       "post_disposal_reopens", *VIEWS}:
        raise ValueError("unexpected checkpoint observation fields")
    if re.fullmatch(r"\d+\.\d+\.\d+", native["rocksdb"]) is None:
        raise ValueError("invalid RocksDB build")
    if (native["sequence"], native["verified_keys"], native["post_disposal_reopens"]) != (128, 64, 2):
        raise ValueError("checkpoint client observations differ")
    sizes: dict[str, int] = {}
    for view in VIEWS:
        if not native[view]:
            raise ValueError("empty checkpoint")
        for key, size in native[view].items():
            if SST.fullmatch(key) is None or type(size) is not int or size <= 0:
                raise ValueError("invalid checkpoint file observation")
            if sizes.setdefault(key, size) != size:
                raise ValueError("shared file changed size")
    archived, before, after = (set(native[name]) for name in VIEWS)
    if (len(archived), len(before), len(after), len(before & after)) != (4, 3, 2, 1):
        raise ValueError("checkpoint compaction shape changed")
    catalog = set(sizes)
    required = required_while_unresolved((before, after))
    reclaimed = catalog - required
    if reclaimed != archived - (before | after) or len(reclaimed) != native["archived_unlinked"]:
        raise ValueError("native archive disposal differs from the two-view prediction")

    policies = {
        "hold-all": catalog, "assume-before": before, "assume-after": after,
        "hold-candidate-union": required,
    }
    scores = {name: {"unsafe_allowed": 0, "safe_blocked": 0, "allowed": 0}
              for name in policies}
    subsets = 0
    for count in range(len(catalog) + 1):
        for removed in map(set, combinations(sorted(catalog), count)):
            subsets += 1
            available = catalog - removed
            safe = before <= available and after <= available
            for name, held in policies.items():
                allowed = removed.isdisjoint(held)
                scores[name]["unsafe_allowed"] += allowed and not safe
                scores[name]["safe_blocked"] += safe and not allowed
                scores[name]["allowed"] += allowed
    if any(scores["hold-candidate-union"][name] for name in ("unsafe_allowed", "safe_blocked")):
        raise ValueError("candidate guard disagrees with the native-view oracle")
    return {
        "catalog_files": len(catalog), "held_files": len(required),
        "archived_only": sorted(reclaimed), "retired_sst_bytes": sum(sizes[f] for f in reclaimed),
        "shared_files": sorted(before & after), "subsets": subsets, "policies": scores,
    }


def checked(value: dict) -> dict:
    if value["sources"] != source_binding() or value["summary"] != evaluate(value["native"]):
        raise ValueError("stale checkpoint evidence")
    return value["summary"]


def capture() -> dict:
    sources = source_binding()
    with tempfile.TemporaryDirectory(prefix="rf-checkpoints-") as folder:
        work = Path(folder)
        binary = work / "observer"
        database = work / "native"
        database.mkdir()
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-O2", "-Wall", "-Wextra",
                        str(SOURCE), "-lrocksdb", "-o", str(binary)], check=True, timeout=180)
        stdout = subprocess.check_output([str(binary), str(database), *map(str, CORPUS[:2])],
                                         text=True, timeout=120)
    if sources != source_binding():
        raise ValueError("checkpoint inputs changed during capture")
    native = json.loads(stdout)
    return {"sources": sources, "native": native, "summary": evaluate(native)}


def macros(value: dict) -> str:
    summary, native = checked(value), value["native"]
    macros = {
        "CheckpointKeys": native["verified_keys"],
        "CheckpointSequence": native["sequence"],
        "CheckpointArchivedFiles": len(native["archive"]),
        "CheckpointBeforeFiles": len(native["before"]),
        "CheckpointAfterFiles": len(native["after"]),
        "CheckpointShared": len(summary["shared_files"]),
        "CheckpointCatalog": summary["catalog_files"],
        "CheckpointHeld": summary["held_files"],
        "CheckpointRetired": len(summary["archived_only"]),
        "CheckpointBytes": format(summary["retired_sst_bytes"], ","),
        "CheckpointReopens": native["post_disposal_reopens"],
        "CheckpointSubsets": summary["subsets"],
        "CheckpointAllBlocked": summary["policies"]["hold-all"]["safe_blocked"],
        "CheckpointOldBad": summary["policies"]["assume-before"]["unsafe_allowed"],
        "CheckpointNewBad": summary["policies"]["assume-after"]["unsafe_allowed"],
    }
    return "% Generated from the retained native checkpoint observations.\n" + "".join(
        f"\\newcommand{{\\{key}}}{{{item}}}\n" for key, item in macros.items())


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--run", action="store_true", help="verify a fresh capture against the receipt")
    cli.add_argument("--record", type=Path, help="capture to a new staging file")
    cli.add_argument("--table", type=Path, help="verify the paper data consumer")
    cli.add_argument("--emit-table", type=Path, help="emit the paper data consumer to a new file")
    args = cli.parse_args()
    if args.record:
        value = capture()
        with args.record.open("x", encoding="utf-8", newline="\n") as out:
            json.dump(value, out, indent=2, sort_keys=True)
            out.write("\n")
    else:
        value = json.loads(RECEIPT.read_bytes())
        if args.run and capture() != value:
            raise ValueError("new checkpoint capture differs from retained evidence")
    rendered = macros(value)
    if args.table and args.table.read_text(encoding="utf-8") != rendered:
        raise ValueError("paper checkpoint counts differ from the native evidence")
    if args.emit_table:
        with args.emit_table.open("x", encoding="utf-8", newline="\n") as out:
            out.write(rendered)
    print(json.dumps(checked(value), sort_keys=True))


if __name__ == "__main__":
    main()
