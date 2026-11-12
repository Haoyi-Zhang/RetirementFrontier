#!/usr/bin/env python3
"""Observe real WAL readers and application-owned file retirement."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile

from publication_handoff import (CORPUS, ROOT, clean, database, durable_directory,
                                 inventory, put_file, references)

RECEIPT = ROOT / "results/reader-pins/observations.json"
ORDERS = ("older-first", "newer-first")
POLICIES = ("wait-all", "pin-aware")
STALE_EXIT = 74


def fixture():
    alpha, beta, archive = (f.read_bytes() for f in CORPUS)
    half, third = len(alpha) // 2, len(alpha) // 3
    views = [
        {"alpha": ["old-left.bin", "old-right.bin"], "beta": ["shared.bin"]},
        {"alpha": ["middle.bin"], "beta": ["shared.bin"]},
        {"alpha": ["latest-left.bin", "latest-right.bin"], "beta": ["shared.bin"]},
    ]
    payloads = {"old-left.bin": alpha[:half], "old-right.bin": alpha[half:],
                "middle.bin": alpha, "latest-left.bin": alpha[:third],
                "latest-right.bin": alpha[third:], "shared.bin": beta, "archive.bin": archive}
    return views, payloads, {"alpha": alpha, "beta": beta}


@contextmanager
def gate(root: Path):
    import fcntl

    # A separate gate covers SQLite snapshot acquisition plus registration,
    # and covers the cleaner's check plus its file removals.
    with (root / "gate").open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def selected(db):
    rows = db.execute("""SELECT selector.epoch, selector.prefix, layouts.manifest
                         FROM selector JOIN layouts USING (epoch)""").fetchall()
    if len(rows) != 1 or rows[0][1] != 1:
        raise ValueError("unexpected selector or client prefix")
    epoch, _, layout = rows[0]
    return epoch, json.loads(layout)


def write_payloads(root: Path, names: set[str], data: dict[str, bytes]) -> None:
    for name in sorted(names):
        path = root / "segments" / name
        if not path.exists():
            put_file(path, data[name])
        elif path.read_bytes() != data[name]:
            raise ValueError("a published segment changed bytes")
    durable_directory(root / "segments")


def initialize(root: Path) -> None:
    views, payloads, _ = fixture()
    (root / "segments").mkdir()
    (root / "pins").mkdir()
    put_file(root / "gate", b"")
    write_payloads(root, references(views[0]) | {"archive.bin"}, payloads)
    with closing(database(root / "catalog.db", "WAL")) as db:
        db.executescript("""CREATE TABLE selector (epoch INTEGER, prefix INTEGER);
                            CREATE TABLE layouts (epoch INTEGER PRIMARY KEY, manifest TEXT NOT NULL);
                            BEGIN;
                            INSERT INTO selector VALUES (0, 1);""")
        db.execute("INSERT INTO layouts VALUES (0, ?)", (json.dumps(views[0], sort_keys=True),))
        db.execute("COMMIT")
    durable_directory(root)


def publish(root: Path, epoch: int) -> None:
    views, data, _ = fixture()
    with gate(root), closing(database(root / "catalog.db", "WAL")) as db:
        if selected(db)[0] != epoch - 1:
            raise ValueError("nonconsecutive publication")
        write_payloads(root, references(views[epoch]), data)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO layouts VALUES (?, ?)",
                   (epoch, json.dumps(views[epoch], sort_keys=True)))
        db.execute("UPDATE selector SET epoch=?", (epoch,))
        db.execute("COMMIT")


def verify_bytes(root: Path, view: dict, expected: dict) -> None:
    references(view)
    actual = {name: b"".join((root / "segments" / p).read_bytes() for p in pieces)
              for name, pieces in view.items()}
    if actual != expected:
        raise ValueError("snapshot recovered different object bytes")


def reader(root: Path, label: str) -> None:
    if label not in ("first", "second"):
        raise ValueError("unknown reader")
    pin = root / "pins" / f"{label}.json"
    _, _, expected = fixture()
    with gate(root):
        db = database(root / "catalog.db")
        db.execute("BEGIN")
        epoch, view = selected(db)  # The first read actually fixes the WAL snapshot.
        put_file(pin, json.dumps({"epoch": epoch, "view": view}, sort_keys=True).encode("ascii"))
        durable_directory(pin.parent)
    print(f"ready {epoch}", flush=True)
    for line in sys.stdin:
        command = line.strip()
        if command == "verify":
            if selected(db) != (epoch, view):
                raise ValueError("reader switched snapshots")
            verify_bytes(root, view, expected)
            print(f"verified {epoch}", flush=True)
        elif command == "release":
            db.close()  # Never unregister a reader that can still read.
            with gate(root):
                pin.unlink()
                durable_directory(pin.parent)
            print("released", flush=True)
            return
        elif command == "exit":
            os._exit(STALE_EXIT)  # The durable pin intentionally survives.
        else:
            raise ValueError("unknown reader command")
    db.close()


def plan(current: dict, pins: list[dict], files: dict[str, int]) -> list[str]:
    needed = set().union(*(references(view) for view in [current, *pins]))
    if not needed <= files.keys():
        raise ValueError("protected segment missing")
    result = sorted(files.keys() - needed)
    if any(not references(view).isdisjoint(result) for view in [current, *pins]):
        raise ValueError("removal overlaps a protected layout")
    return result


def collect(root: Path, policy: str) -> dict:
    with gate(root), closing(database(root / "catalog.db")) as db:
        epoch, current = selected(db)
        if epoch != 2 or db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("unconfirmed publication or invalid catalog")
        pins = []
        for p in sorted((root / "pins").iterdir()):
            if p.name not in {"first.json", "second.json"} or p.is_symlink():
                raise ValueError("unrecognized registration")
            value = json.loads(p.read_bytes())
            registered = db.execute("SELECT manifest FROM layouts WHERE epoch=?",
                                    (value["epoch"],)).fetchone()
            if registered is None or json.loads(registered[0]) != value["view"]:
                raise ValueError("invalid registered layout")
            pins.append(value)
        files = inventory(root)
        protected = [p["view"] for p in pins]
        checked = plan(current, protected, files)
        if policy not in POLICIES:
            raise ValueError("unknown cleanup policy")
        deletion = checked if policy == "pin-aware" or not pins else []
        clean(root, deletion)
        verify_bytes(root, current, fixture()[2])
        return {"pin_epochs": sorted(p["epoch"] for p in pins),
                "removed": deletion, "bytes_removed": sum(files[n] for n in deletion),
                "remaining": sorted(inventory(root)), "current": epoch}


def expect(child, message: str) -> None:
    if not select.select([child.stdout], [], [], 20)[0] or child.stdout.readline().strip() != message:
        raise RuntimeError(f"reader did not report {message}")


def request(child, command: str, message: str) -> None:
    child.stdin.write(command + "\n")
    child.stdin.flush()
    expect(child, message)


@contextmanager
def launch(root: Path, label: str, epoch: int):
    child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                              "--reader", str(root), label], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    try:
        expect(child, f"ready {epoch}")
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        child.stdin.close()
        child.stdout.close()


def exercise(order: str, policy: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="rf-wal-reader-pins-") as directory, ExitStack() as readers:
        root = Path(directory)
        initialize(root)
        first = readers.enter_context(launch(root, "first", 0))
        publish(root, 1)
        second = readers.enter_context(launch(root, "second", 1))
        publish(root, 2)
        both = collect(root, policy)
        request(first, "verify", "verified 0")
        request(second, "verify", "verified 1")
        early, last, remaining_epoch = (first, second, 1) if order == "older-first" else (second, first, 0)
        request(early, "release", "released")
        if early.wait(timeout=10) != 0:
            raise RuntimeError("reader did not exit cleanly")
        one = collect(root, policy)
        request(last, "verify", f"verified {remaining_epoch}")
        request(last, "release", "released")
        if last.wait(timeout=10) != 0:
            raise RuntimeError("reader did not exit cleanly")
        none = collect(root, policy)
    return {"order": order, "policy": policy, "phases": [both, one, none],
            "older_snapshot_reads": 2 if order == "newer-first" else 1,
            "middle_snapshot_reads": 2 if order == "older-first" else 1}


def exercise_stale() -> dict:
    with tempfile.TemporaryDirectory(prefix="rf-wal-stale-pin-") as directory:
        root = Path(directory)
        initialize(root)
        with launch(root, "first", 0) as child:
            publish(root, 1)
            publish(root, 2)
            child.stdin.write("exit\n")
            child.stdin.flush()
            if child.wait(timeout=10) != STALE_EXIT:
                raise RuntimeError("expected an exit without unregistration")
        return collect(root, "pin-aware")


def summarize(rows: list[dict], stale: dict) -> dict:
    views, data, _ = fixture()
    refs = [references(v) for v in views]
    if [(r["order"], r["policy"]) for r in rows] != [(o, p) for o in ORDERS for p in POLICIES]:
        raise ValueError("incomplete pin trial matrix")
    outcomes = {}
    for row in rows:
        remaining = set(data)
        order, policy = row["order"], row["policy"]
        surviving = [0, 1] if order == "older-first" else [1, 0]
        expected_reads = (1, 2) if order == "older-first" else (2, 1)
        if (row["older_snapshot_reads"], row["middle_snapshot_reads"]) != expected_reads:
            raise ValueError("missing native snapshot read")
        for phase, active in zip(row["phases"], ([0, 1], [surviving[1]], []), strict=True):
            protected = refs[2].union(*(refs[i] for i in active))
            removed = (remaining - protected) if policy == "pin-aware" or not active else set()
            remaining -= removed
            expected = {"current": 2, "pin_epochs": active, "removed": sorted(removed),
                        "remaining": sorted(remaining), "bytes_removed": sum(len(data[n]) for n in removed)}
            if phase != expected:
                raise ValueError("pin phase differs from the independent live-set calculation")
        if policy == "pin-aware":
            outcomes[order] = [phase["bytes_removed"] for phase in row["phases"]]
    stale_held = refs[0] | refs[2]
    stale_removed = set(data) - stale_held
    if stale != {"current": 2, "pin_epochs": [0], "removed": sorted(stale_removed),
                 "remaining": sorted(stale_held), "bytes_removed": sum(len(data[n]) for n in stale_removed)}:
        raise ValueError("stale pin was silently discarded")
    return {"trials": len(rows), "snapshot_reads": sum(r["older_snapshot_reads"] +
                                                       r["middle_snapshot_reads"] for r in rows),
            "selector_only_protected_files": len((refs[0] | refs[1]) - refs[2]),
            "pin_aware_bytes": outcomes, "stale_protected_files": len(refs[0] - refs[2])}


def bindings() -> dict:
    files = (Path(__file__).resolve(), ROOT / "scripts/publication_handoff.py", *CORPUS)
    return {f.relative_to(ROOT).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in files}


def checked(value: dict) -> dict:
    if value["sources"] != bindings() or summarize(value["trials"], value["stale"]) != value["summary"]:
        raise ValueError("stale reader pin evidence")
    return value["summary"]


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--reader", nargs=2, help=argparse.SUPPRESS)
    cli.add_argument("--record", type=Path)
    cli.add_argument("--run", action="store_true")
    cli.add_argument("--table", type=Path)
    cli.add_argument("--emit-table", type=Path)
    args = cli.parse_args()
    if args.reader:
        reader(Path(args.reader[0]), args.reader[1])
        return
    if args.run or args.record:
        sources = bindings()
        rows = [exercise(order, policy) for order in ORDERS for policy in POLICIES]
        stale = exercise_stale()
        value = {"sources": sources, "trials": rows, "stale": stale, "summary": summarize(rows, stale)}
        checked(value)
        if args.record:
            with args.record.open("x", encoding="utf-8", newline="\n") as out:
                json.dump(value, out, indent=2, sort_keys=True)
                out.write("\n")
        elif value != json.loads(RECEIPT.read_bytes()):
            raise ValueError("native reader replay differs from the retained evidence")
    else:
        value = json.loads(RECEIPT.read_bytes())
    summary = checked(value)
    before, after_older, after_newer = summary["pin_aware_bytes"]["older-first"]
    opposite = summary["pin_aware_bytes"]["newer-first"]
    if opposite != [before, after_newer, after_older]:
        raise ValueError("release order changed the byte accounting")
    fields = {"PinTrials": summary["trials"], "PinReads": summary["snapshot_reads"],
              "PinOldBytes": after_older, "PinMiddleBytes": after_newer, "PinArchiveBytes": before,
              "PinSelectorFiles": summary["selector_only_protected_files"],
              "PinStaleFiles": summary["stale_protected_files"]}
    rendered = "% Generated from the retained WAL reader observations.\n" + "".join(
        f"\\newcommand{{\\{key}}}{{{item:,}}}\n" for key, item in sorted(fields.items()))
    if args.table and args.table.read_text(encoding="utf-8") != rendered:
        raise ValueError("paper reader counts differ from the retained evidence")
    if args.emit_table:
        with args.emit_table.open("x", encoding="utf-8", newline="\n") as out:
            out.write(rendered)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
