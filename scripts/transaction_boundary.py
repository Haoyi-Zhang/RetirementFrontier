#!/usr/bin/env python3
"""Separate SQLite integrity from object recovery in owned temporary fixtures."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import publication_handoff as handoff

RECEIPT = handoff.ROOT / "results/publication-handoff/transaction-boundary.json"
POLICIES = ("keep-before", "keep-after", "keep-both")


def probe(root: Path, ticket: dict, expected: dict) -> dict:
    with closing(handoff.database(root / "catalog.db")) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        epoch, prefix = db.execute("SELECT epoch, prefix FROM selector WHERE id=1").fetchone()
        layouts = [(n, json.loads(v)) for n, v in db.execute(
            "SELECT epoch, manifest FROM layouts ORDER BY epoch")]
    if integrity != [("ok",)] or epoch not in (0, 1) or prefix != ticket["client_prefix"]:
        raise ValueError("unexpected native catalog outcome")
    if layouts != [(n, ticket["views"][str(n)]) for n in range(epoch + 1)]:
        raise ValueError("selector and complete layout did not change atomically")
    missing, exact = set(), []
    for key, pieces in layouts[-1][1].items():
        payload, complete = [], True
        for piece in pieces:
            try:
                payload.append((root / "segments" / piece).read_bytes())
            except FileNotFoundError:
                missing.add(piece)
                complete = False
        if complete:
            if b"".join(payload) != expected[key]:
                raise ValueError("object bytes disagree despite complete segment reads")
            exact.append(key)
    return {"sqlite_integrity": "ok", "epoch": epoch, "client_prefix": prefix,
            "catalog_layouts": [n for n, _ in layouts],
            "missing_segments": sorted(missing), "exact_objects": sorted(exact)}


def run_case(mode: str, shape: str, boundary: str, policy: str) -> dict:
    ticket, data, expected = handoff.fixture(shape)
    with tempfile.TemporaryDirectory(prefix="rf-owned-txn-") as tmp:
        root = Path(tmp)
        handoff.populate(root, mode, ticket, data)
        exited = subprocess.run([sys.executable, "-B", handoff.__file__, "--child",
                                 str(root), mode, boundary], capture_output=True, timeout=30)
        if (exited.returncode, exited.stdout, exited.stderr) != (handoff.NO_REPLY, b"", b""):
            raise ValueError("publisher did not exit at the declared no-reply boundary")
        pending = json.loads((root / "pending.json").read_bytes())
        present = handoff.inventory(root)
        if pending != ticket or present != {name: len(content) for name, content in data.items()}:
            raise ValueError("cleaner inputs changed before outcome resolution")
        if policy == "keep-both":
            plan = handoff.certify_pending(pending, present)
        else:
            guess = {"keep-before": "0", "keep-after": "1"}[policy]
            plan = sorted(present.keys() - handoff.references(pending["views"][guess]))
        # Only files created above, in this fixture's temporary directory.
        handoff.clean(root, plan)
        return dict(mode=mode, shape=shape, boundary=boundary, policy=policy,
                    removed=plan, **probe(root, ticket, expected))


def matrix():
    return [(m, s, b, p) for m in handoff.MODES for s in handoff.SHAPES
            for b in handoff.BOUNDARIES for p in POLICIES]


def summarize(rows: list[dict]) -> dict:
    fields = ("mode", "shape", "boundary", "policy")
    if [tuple(r[k] for k in fields) for r in rows] != matrix():
        raise ValueError("incomplete transaction-boundary matrix")
    counts = {policy: {"trials": 0, "object_failures": 0} for policy in POLICIES}
    for row in rows:
        _, shape, boundary, policy = (row[k] for k in fields)
        ticket, files, _ = handoff.fixture(shape)
        epoch = int(boundary == "after-commit")
        views = ticket["views"]
        selected = views[str(epoch)]
        if (row["sqlite_integrity"], row["epoch"], row["client_prefix"],
                row["catalog_layouts"]) != ("ok", epoch, ticket["client_prefix"], list(range(epoch + 1))):
            raise ValueError("native catalog evidence contradicts the transaction boundary")
        kept = set().union(*(handoff.references(views[n]) for n in (
            ("0", "1") if policy == "keep-both" else ("0",) if policy == "keep-before" else ("1",))))
        removed = files.keys() - kept
        needed = handoff.references(selected)
        exact = sorted(k for k, pieces in selected.items() if removed.isdisjoint(pieces))
        if (row["removed"], row["missing_segments"], row["exact_objects"]) != (
                sorted(removed), sorted(needed & removed), exact):
            raise ValueError("actual file probes disagree with the selected layout and policy")
        counts[policy]["trials"] += 1
        counts[policy]["object_failures"] += bool(row["missing_segments"])
    return {"trials": len(rows), "intact_catalogs": len(rows), "policies": counts}


def binding() -> dict:
    local = Path(__file__).resolve()
    return {**handoff.binding(),
            local.relative_to(handoff.ROOT).as_posix(): hashlib.sha256(local.read_bytes()).hexdigest()}


def checked(receipt: dict) -> dict:
    if (set(receipt) != {"sources", "observations", "summary"} or receipt["sources"] != binding()
            or summarize(receipt["observations"]) != receipt["summary"]):
        raise ValueError("transaction-boundary evidence is stale or inconsistent")
    return receipt["summary"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--record", type=Path)
    parser.add_argument("--table", type=Path)
    parser.add_argument("--emit-table", type=Path)
    args = parser.parse_args()
    if args.run or args.record:
        sources = binding()
        rows = [run_case(*entry) for entry in matrix()]
        receipt = {"sources": sources, "observations": rows, "summary": summarize(rows)}
        checked(receipt)
        if args.record:
            with args.record.open("x", encoding="utf-8", newline="\n") as out:
                json.dump(receipt, out, indent=2, sort_keys=True)
                out.write("\n")
        elif receipt != json.loads(RECEIPT.read_bytes()):
            raise ValueError("native transaction replay differs from the retained evidence")
    else:
        receipt = json.loads(RECEIPT.read_bytes())
    summary = checked(receipt)
    fields = {"BoundaryTrials": summary["trials"], "BoundaryIntact": summary["intact_catalogs"]}
    for policy, row in summary["policies"].items():
        suffix = policy.split("-")[-1].title()
        fields["Boundary" + suffix + "Trials"] = row["trials"]
        fields["Boundary" + suffix + "Failures"] = row["object_failures"]
    table = "% Generated from the native transaction-boundary observations.\n" + "".join(
        f"\\newcommand{{\\{key}}}{{{value:,}}}\n" for key, value in sorted(fields.items()))
    if args.table and args.table.read_text(encoding="utf-8") != table:
        raise ValueError("paper counts differ from the native transaction-boundary observations")
    if args.emit_table:
        with args.emit_table.open("x", encoding="utf-8", newline="\n") as out:
            out.write(table)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
