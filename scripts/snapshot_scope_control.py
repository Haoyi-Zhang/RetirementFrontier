#!/usr/bin/env python3
"""Compare WAL snapshot coverage for SQLite rows and application-owned files."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile

import atomic_reclamation_control as atomic
import publication_handoff as handoff

RECEIPT = handoff.ROOT / "results/reader-pins/snapshot-scope-control.json"
POLICIES = ("sqlite-rows", "current-only", "retain-reader")


def read_view(db, root, rows, expected):
    epoch, prefix, manifest = db.execute(
        "SELECT epoch, prefix, manifest FROM selector JOIN layouts USING (epoch)"
    ).fetchone()
    missing, exact = set(), []
    for name, parts in json.loads(manifest).items():
        payloads = []
        for part in parts:
            if rows:
                result = db.execute("SELECT payload FROM segments WHERE name=?", (part,)).fetchone()
                data = None if result is None else result[0]
            else:
                try:
                    data = (root / "segments" / part).read_bytes()
                except FileNotFoundError:
                    data = None
            if data is None:
                missing.add(part)
            else:
                payloads.append(data)
        if len(payloads) == len(parts):
            if b"".join(payloads) != expected[name]:
                raise ValueError("object bytes differ from the original input")
            exact.append(name)
    return dict(epoch=epoch, prefix=prefix, missing=sorted(missing), exact=sorted(exact))


def run_case(shape, policy):
    if policy not in POLICIES:
        raise ValueError("unknown snapshot policy")
    ticket, data, expected = handoff.fixture(shape)
    old, new = (handoff.references(ticket["views"][str(i)]) for i in (0, 1))
    retired = sorted(old - new)
    rows = policy == "sqlite-rows"
    with tempfile.TemporaryDirectory(prefix="rf-snapshot-scope-") as directory:
        root = Path(directory)
        path = root / "catalog.db"
        handoff.populate(root, "WAL", ticket, {} if rows else data)
        with closing(handoff.database(path, "WAL")) as writer, closing(handoff.database(path)) as reader:
            if rows:
                writer.execute("CREATE TABLE segments(name TEXT PRIMARY KEY, payload BLOB NOT NULL)")
                writer.executemany("INSERT INTO segments VALUES (?,?)", sorted(data.items()))

            def inventory():
                if rows:
                    return sorted(n for n, in writer.execute("SELECT name FROM segments"))
                return sorted(handoff.inventory(root))

            reader.execute("BEGIN")
            # Establish the snapshot by reading its selector, not its object
            # bytes. The first payload read is after retirement.
            pinned = reader.execute("SELECT epoch FROM selector WHERE id=1").fetchone()[0]
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO layouts VALUES (1, ?)",
                           (json.dumps(ticket["views"]["1"], sort_keys=True),))
            writer.execute("UPDATE selector SET epoch=1 WHERE id=1 AND epoch=0")
            if rows:
                for name in retired:
                    if writer.execute("DELETE FROM segments WHERE name=?", (name,)).rowcount != 1:
                        raise ValueError("expected to retire exactly one row")
            writer.execute("COMMIT")
            if policy == "current-only":
                handoff.clean(root, retired)
            elif policy == "retain-reader":
                plan = handoff.certify_pending(ticket, handoff.inventory(root))
                if set(plan) & (old | new):
                    raise ValueError("guard removed a protected segment")
                handoff.clean(root, plan)
            with closing(handoff.database(path)) as fresh:
                if fresh.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("invalid committed catalog")
                latest = read_view(fresh, root, rows, expected)
            older = read_view(reader, root, rows, expected)
            during = inventory()
            reader.execute("ROLLBACK")
            if policy == "retain-reader":
                handoff.clean(root, retired)
            after = inventory()
            after_release = read_view(writer, root, rows, expected)
    return dict(shape=shape, policy=policy, pinned=pinned, old=older, fresh=latest,
                during=during, after=after, after_release=after_release)


def matrix():
    return [(shape, policy) for shape in handoff.SHAPES for policy in POLICIES]


def summarize(trials):
    if [(r["shape"], r["policy"]) for r in trials] != matrix():
        raise ValueError("incomplete or duplicate snapshot-scope cells")
    summary = {p: dict(trials=0, old_exact=0, new_exact=0, release_exact=0) for p in POLICIES}
    for row in trials:
        policy = row["policy"]
        ticket, data, expected = handoff.fixture(row["shape"])
        old, new = (handoff.references(ticket["views"][str(i)]) for i in (0, 1))
        retiring = old - new
        during = old | new if policy == "retain-reader" else data.keys() - retiring
        after = new if policy == "retain-reader" else during

        def view(epoch, absent):
            parts = ticket["views"][str(epoch)]
            return dict(epoch=epoch, prefix=ticket["client_prefix"], missing=sorted(absent),
                        exact=sorted(n for n in expected if absent.isdisjoint(parts[n])))

        if (row["pinned"] != 0 or row["old"] != view(0, retiring if policy == "current-only" else set())
                or row["fresh"] != view(1, set()) or row["after_release"] != view(1, set())
                or row["during"] != sorted(during) or row["after"] != sorted(after)):
            raise ValueError("native snapshot observations differ from the lifetime oracle")
        item = summary[policy]
        item["trials"] += 1
        for name, key in (("old_exact", "old"), ("new_exact", "fresh"), ("release_exact", "after_release")):
            item[name] += row[key]["exact"] == sorted(expected)
    return summary


def binding():
    path = Path(__file__).resolve()
    return {**atomic.binding(), path.relative_to(handoff.ROOT).as_posix():
            hashlib.sha256(path.read_bytes()).hexdigest()}


def checked(receipt):
    if (set(receipt) != {"sources", "trials", "summary"} or receipt["sources"] != binding()
            or summarize(receipt["trials"]) != receipt["summary"]):
        raise ValueError("snapshot-scope receipt is stale or inconsistent")
    return receipt["summary"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--record", type=Path)
    parser.add_argument("--table", type=Path)
    parser.add_argument("--emit-table", type=Path)
    args = parser.parse_args()
    if args.run or args.record:
        sources = binding()
        trials = [run_case(*case) for case in matrix()]
        receipt = dict(sources=sources, trials=trials, summary=summarize(trials))
        summary = checked(receipt)
        if args.record:
            with args.record.open("x", encoding="utf-8", newline="\n") as out:
                json.dump(receipt, out, indent=2, sort_keys=True)
                out.write("\n")
        elif receipt != json.loads(RECEIPT.read_bytes()):
            raise ValueError("native snapshot-scope rerun differs from retained evidence")
    else:
        summary = checked(json.loads(RECEIPT.read_bytes()))
    fields = {}
    for policy, label in zip(POLICIES, ("Rows", "Current", "Guard"), strict=True):
        fields[f"Snapshot{label}Trials"] = summary[policy]["trials"]
        fields[f"Snapshot{label}OldExact"] = summary[policy]["old_exact"]
    table = "% Generated from the checked WAL snapshot observations.\n" + "".join(
        f"\\newcommand{{\\{name}}}{{{value}}}\n" for name, value in sorted(fields.items()))
    if args.table and args.table.read_text(encoding="utf-8") != table:
        raise ValueError("paper counts disagree with WAL snapshot observations")
    if args.emit_table:
        with args.emit_table.open("x", encoding="utf-8", newline="\n") as out:
            out.write(table)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
