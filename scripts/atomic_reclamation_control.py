#!/usr/bin/env python3
"""Compare transaction-owned and external retirement on disposable SQLite stores."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import publication_handoff as handoff

RECEIPT = handoff.ROOT / "results/publication-handoff/atomic-reclamation-control.json"
DOMAINS = ("sqlite-rows", "external-files")


def retiring(ticket):
    before, after = (handoff.references(ticket["views"][i]) for i in ("0", "1"))
    return sorted(before - after)


def worker(root, mode, boundary, domain):
    if boundary not in handoff.BOUNDARIES or domain not in DOMAINS:
        raise ValueError("unknown experiment condition")
    ticket = json.loads((root / "pending.json").read_bytes())
    with closing(handoff.database(root / "catalog.db", mode)) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO layouts VALUES (1, ?)",
                   (json.dumps(ticket["views"]["1"], sort_keys=True),))
        db.execute("UPDATE selector SET epoch=1 WHERE id=1 AND epoch=0")
        if domain == "sqlite-rows":
            for name in retiring(ticket):
                if db.execute("DELETE FROM segments WHERE name=?", (name,)).rowcount != 1:
                    raise ValueError("transaction did not retire the expected row")
        else:
            handoff.clean(root, retiring(ticket))
        if boundary == "after-commit":
            db.execute("COMMIT")
        os._exit(handoff.NO_REPLY)


def inspect(root, domain, ticket, data, expected):
    with closing(handoff.database(root / "catalog.db")) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        epoch, prefix = db.execute("SELECT epoch, prefix FROM selector WHERE id=1").fetchone()
        layouts = [(i, json.loads(m)) for i, m in db.execute(
            "SELECT epoch, manifest FROM layouts ORDER BY epoch")]
        if domain == "sqlite-rows":
            observed = dict(db.execute("SELECT name, payload FROM segments"))
        else:
            observed = {name: (root / "segments" / name).read_bytes()
                        for name in handoff.inventory(root)}
    if integrity != [("ok",)] or epoch not in (0, 1) or prefix != ticket["client_prefix"]:
        raise ValueError("unexpected native catalog state")
    if layouts != [(i, ticket["views"][str(i)]) for i in range(epoch + 1)]:
        raise ValueError("mixed selector/layout state")
    if any(data.get(name) != value for name, value in observed.items()):
        raise ValueError("stored segment bytes changed")
    exact, missing = [], set()
    for name, pieces in layouts[-1][1].items():
        absent = set(pieces) - observed.keys()
        if absent:
            missing.update(absent)
        elif b"".join(observed[piece] for piece in pieces) == expected[name]:
            exact.append(name)
        else:
            raise ValueError("recovered object bytes changed")
    return dict(sqlite_integrity="ok", epoch=epoch, client_prefix=prefix,
                layout_epochs=[i for i, _ in layouts], remaining=sorted(observed),
                missing=sorted(missing), exact_objects=sorted(exact))


def run_case(mode, shape, boundary, domain):
    ticket, data, expected = handoff.fixture(shape)
    with tempfile.TemporaryDirectory(prefix="rf-atomic-control-") as directory:
        root = Path(directory)
        # Both arms start with identical logical bytes and metadata. Only one
        # copy of the segment payloads is authoritative in either arm.
        handoff.populate(root, mode, ticket, data if domain == "external-files" else {})
        if domain == "sqlite-rows":
            with closing(handoff.database(root / "catalog.db", mode)) as db:
                db.execute("CREATE TABLE segments(name TEXT PRIMARY KEY, payload BLOB NOT NULL)")
                db.execute("BEGIN IMMEDIATE")
                db.executemany("INSERT INTO segments VALUES (?,?)", sorted(data.items()))
                db.execute("COMMIT")
        control = inspect(root, domain, ticket, data, expected)
        if control["epoch"] != 0 or control["missing"] or control["exact_objects"] != sorted(expected):
            raise ValueError("baseline does not recover the original bytes")
        result = subprocess.run([sys.executable, "-B", __file__, "--child",
                                 str(root), mode, boundary, domain],
                                timeout=20, capture_output=True)
        if (result.returncode, result.stdout, result.stderr) != (handoff.NO_REPLY, b"", b""):
            raise ValueError("worker did not stop at the declared boundary: " +
                             result.stderr.decode(errors="replace"))
        return dict(mode=mode, shape=shape, boundary=boundary, domain=domain,
                    baseline=control, observed=inspect(root, domain, ticket, data, expected))


KEYS = ("mode", "shape", "boundary", "domain")


def matrix():
    return [(m, s, b, d) for m in handoff.MODES for s in handoff.SHAPES
            for b in handoff.BOUNDARIES for d in DOMAINS]


def summarize(rows):
    if [tuple(row[k] for k in KEYS) for row in rows] != matrix():
        raise ValueError("incomplete or duplicate transaction-scope cells")
    counts = {d: {b: {"trials": 0, "exact": 0} for b in handoff.BOUNDARIES} for d in DOMAINS}
    for row in rows:
        _, shape, boundary, domain = (row[k] for k in KEYS)
        ticket, data, expected = handoff.fixture(shape)
        def oracle(epoch, remaining):
            selected = ticket["views"][str(epoch)]
            missing = handoff.references(selected) - remaining
            return dict(sqlite_integrity="ok", epoch=epoch, client_prefix=ticket["client_prefix"],
                        layout_epochs=list(range(epoch + 1)), remaining=sorted(remaining),
                        missing=sorted(missing),
                        exact_objects=sorted(name for name, pieces in selected.items()
                                             if set(pieces) <= remaining))
        if row["baseline"] != oracle(0, data.keys()):
            raise ValueError("baseline mismatch")
        epoch = int(boundary == "after-commit")
        removed = set(retiring(ticket)) if epoch or domain == "external-files" else set()
        if row["observed"] != oracle(epoch, data.keys() - removed):
            raise ValueError("observations contradict the scope/cut oracle")
        cell = counts[domain][boundary]
        cell["trials"] += 1
        cell["exact"] += row["observed"]["exact_objects"] == sorted(expected)
    return {"trials": len(rows), "intact_catalogs": len(rows), "domains": counts}


def binding():
    files = (Path(__file__).resolve(),)
    return {**handoff.binding(), **{p.relative_to(handoff.ROOT).as_posix():
                                    hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}


def checked(receipt):
    if (set(receipt) != {"sources", "observations", "summary"} or receipt["sources"] != binding()
            or summarize(receipt["observations"]) != receipt["summary"]):
        raise ValueError("transaction-scope receipt is stale or inconsistent")
    return receipt["summary"]


def macros(summary):
    fields = {"ScopeTrials": summary["trials"], "ScopeIntact": summary["intact_catalogs"]}
    for domain, label in zip(DOMAINS, ("Atomic", "External"), strict=True):
        for boundary, stage in zip(handoff.BOUNDARIES, ("Before", "After"), strict=True):
            for field, value in summary["domains"][domain][boundary].items():
                fields[f"Scope{label}{stage}{field.title()}"] = value
    return "% Generated from the checked transaction-scope observations.\n" + "".join(
        f"\\newcommand{{\\{name}}}{{{value}}}\n" for name, value in sorted(fields.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run", action="store_true")
    group.add_argument("--record", type=Path)
    group.add_argument("--child", nargs=4)
    parser.add_argument("--table", type=Path)
    parser.add_argument("--emit-table", type=Path)
    args = parser.parse_args()
    if args.child:
        root, mode, boundary, domain = args.child
        worker(Path(root), mode, boundary, domain)
        raise RuntimeError("worker returned instead of exiting at the declared cut")
    if args.run or args.record:
        sources = binding()
        rows = [run_case(*case) for case in matrix()]
        receipt = dict(sources=sources, observations=rows, summary=summarize(rows))
        summary = checked(receipt)
        if args.record:
            with args.record.open("x", encoding="utf-8", newline="\n") as out:
                json.dump(receipt, out, indent=2, sort_keys=True)
                out.write("\n")
        elif receipt != json.loads(RECEIPT.read_bytes()):
            raise ValueError("native transaction-scope rerun differs from retained evidence")
    else:
        summary = checked(json.loads(RECEIPT.read_bytes()))
    table = macros(summary)
    if args.table and args.table.read_text(encoding="utf-8") != table:
        raise ValueError("paper counts disagree with native transaction-scope observations")
    if args.emit_table:
        with args.emit_table.open("x", encoding="utf-8", newline="\n") as out:
            out.write(table)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
