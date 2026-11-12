#!/usr/bin/env python3
"""Exercise unresolved publication and real reader registration together."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from publication_handoff import NO_REPLY
from reader_pins import (CORPUS, ROOT, clean, database, durable_directory, expect,
                         fixture, gate, initialize, inventory, launch, plan, publish,
                         put_file, reader, references, request, selected, verify_bytes,
                         write_payloads)

RECEIPT = ROOT / "results/combined-reclamation/observations.json"
OUTCOMES = ("before-commit", "after-commit")
ORDERS = ("cleanup-first", "reader-first")
POLICIES = ("resolve-first", "combined")


def prepare(root: Path) -> None:
    views, data, _ = fixture()
    with gate(root), closing(database(root / "catalog.db")) as db:
        if selected(db) != (1, views[1]):
            raise ValueError("unexpected base publication")
        write_payloads(root, references(views[2]), data)
        ticket = {"state": "pending", "candidates": [1, 2], "layouts": views}
        put_file(root / "handoff.json", json.dumps(ticket, sort_keys=True).encode("ascii"))
        durable_directory(root)


def writer(root: Path, outcome: str) -> None:
    with gate(root):
        ticket = json.loads((root / "handoff.json").read_bytes())
        validate_ticket(ticket)
        if ticket["state"] != "pending":
            raise ValueError("publication ticket is not pending")
        db = database(root / "catalog.db", "WAL")
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO layouts VALUES (2, ?)",
                   (json.dumps(ticket["layouts"][2], sort_keys=True),))
        db.execute("UPDATE selector SET epoch=2 WHERE epoch=1")
        if outcome == "after-commit":
            db.execute("COMMIT")
        elif outcome != "before-commit":
            raise ValueError("unknown interruption point")
        os._exit(NO_REPLY)


def newcomer(root: Path) -> None:
    import fcntl

    # Report actual lock contention, then enter the ordinary registration path.
    with (root / "gate").open("r+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("blocked", flush=True)
        else:
            fcntl.flock(lock, fcntl.LOCK_UN)
            print("available", flush=True)
    if sys.stdin.readline().strip() != "register":
        raise ValueError("expected registration command")
    reader(root, "second")


@contextmanager
def launch_newcomer(root: Path):
    child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                              "--newcomer", str(root)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    try:
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


def validate_ticket(ticket: dict) -> None:
    if not isinstance(ticket, dict) or set(ticket) != {"state", "candidates", "layouts"}:
        raise ValueError("invalid publication ticket")
    if not isinstance(ticket["candidates"], list) or any(type(e) is not int for e in ticket["candidates"]):
        raise ValueError("invalid candidate epochs")
    if ((ticket["state"] == "pending" and ticket["candidates"] != [1, 2])
            or (ticket["state"] == "resolved" and ticket["candidates"] not in ([1], [2]))
            or ticket["state"] not in ("pending", "resolved")):
        raise ValueError("incomplete or invalid candidate set")
    if not isinstance(ticket["layouts"], list) or len(ticket["layouts"]) != 3:
        raise ValueError("incomplete layout registry")
    for view in ticket["layouts"]:
        references(view)


def protected_plan(ticket: dict, pin_epochs: list[int], files: dict[str, int]) -> list[str]:
    validate_ticket(ticket)
    if any(type(e) is not int or e not in range(3) for e in pin_epochs):
        raise ValueError("invalid pin epoch")
    required = [ticket["layouts"][e] for e in [*ticket["candidates"], *pin_epochs]]
    return plan(required[0], required[1:], files)


def collect_locked(root: Path, policy: str) -> dict:
    """Caller owns the application gate. No access to SQLite or its journal."""
    if policy not in POLICIES:
        raise ValueError("unknown cleanup policy")
    ticket = json.loads((root / "handoff.json").read_bytes())
    validate_ticket(ticket)
    pins = []
    for path in sorted((root / "pins").iterdir()):
        if path.name not in ("first.json", "second.json") or path.is_symlink():
            raise ValueError("unrecognized pin registration")
        pin = json.loads(path.read_bytes())
        epoch = pin["epoch"]
        if type(epoch) is not int or epoch not in range(3) or ticket["layouts"][epoch] != pin["view"]:
            raise ValueError("pin does not bind a registered immutable layout")
        pins.append(epoch)
    pins.sort()
    files = inventory(root)
    safe = protected_plan(ticket, pins, files)
    remove = [] if policy == "resolve-first" and ticket["state"] == "pending" else safe
    clean(root, remove)
    return {"state": ticket["state"], "candidates": ticket["candidates"],
            "pins": pins, "removed": remove, "remaining": sorted(inventory(root)),
            "bytes_removed": sum(files[name] for name in remove)}


def resolve_locked(root: Path) -> int:
    current = root / "handoff.json"
    ticket = json.loads(current.read_bytes())
    validate_ticket(ticket)
    if ticket["state"] != "pending":
        raise ValueError("publication is not awaiting resolution")
    with closing(database(root / "catalog.db")) as db:
        epoch, view = selected(db)
        if epoch not in ticket["candidates"]:
            raise ValueError("resolved epoch is not a candidate")
        expected = ticket["layouts"][epoch]
        rows = db.execute("SELECT epoch FROM layouts ORDER BY epoch").fetchall()
        if (db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
                or view != expected
                or rows != [(n,) for n in range(epoch + 1)]):
            raise ValueError("SQLite did not recover an atomic selector and layout")
        verify_bytes(root, expected, fixture()[2])
    ticket.update(state="resolved", candidates=[epoch])
    temporary = current.with_suffix(".next")
    put_file(temporary, json.dumps(ticket, sort_keys=True).encode("ascii"))
    os.replace(temporary, current)
    durable_directory(root)
    return epoch


def release(child) -> None:
    request(child, "release", "released")
    if child.wait(timeout=10) != 0:
        raise ValueError("reader did not release cleanly")


def experiment(outcome: str, order: str, policy: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="rf-combined-") as tmp, ExitStack() as readers:
        root = Path(tmp)
        initialize(root)
        oldest = readers.enter_context(launch(root, "first", 0))
        publish(root, 1)
        prepare(root)
        run = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                              "--writer", str(root), outcome], capture_output=True, timeout=30)
        if run.returncode != NO_REPLY or run.stdout or run.stderr:
            raise ValueError("writer produced an unexpected result or completion reply")
        epoch = 2 if outcome == "after-commit" else 1
        if order == "cleanup-first":
            with gate(root):
                latest = readers.enter_context(launch_newcomer(root))
                expect(latest, "blocked")
                latest.stdin.write("register\n")
                latest.stdin.flush()
                unresolved = collect_locked(root, policy)
            expect(latest, f"ready {epoch}")
            admission = "blocked"
        else:
            latest = readers.enter_context(launch_newcomer(root))
            expect(latest, "available")
            request(latest, "register", f"ready {epoch}")
            with gate(root):
                unresolved = collect_locked(root, policy)
            admission = "available"
        request(oldest, "verify", "verified 0")
        request(latest, "verify", f"verified {epoch}")
        with gate(root):
            if resolve_locked(root) != epoch:
                raise ValueError("unexpected resolved publication")
            resolved = collect_locked(root, policy)
        request(oldest, "verify", "verified 0")
        request(latest, "verify", f"verified {epoch}")
        release(latest)
        release(oldest)
        with gate(root):
            drained = collect_locked(root, policy)
            with closing(database(root / "catalog.db")) as db:
                if selected(db)[0] != epoch:
                    raise ValueError("final cleanup changed the selector")
                verify_bytes(root, selected(db)[1], fixture()[2])
    return {"outcome": outcome, "order": order, "policy": policy,
            "exit": run.returncode, "admission": admission, "new_reader_epoch": epoch,
            "snapshot_reads": 4, "phases": [unresolved, resolved, drained]}


def summarize(rows: list[dict]) -> dict:
    layouts, payload, _ = fixture()
    refs = [references(view) for view in layouts]
    matrix = [(a, b, c) for a in OUTCOMES for b in ORDERS for c in POLICIES]
    if [tuple(r[k] for k in ("outcome", "order", "policy")) for r in rows] != matrix:
        raise ValueError("incomplete combined trial matrix")
    for row in rows:
        epoch = 2 if row["outcome"] == "after-commit" else 1
        first = row["order"] == "cleanup-first"
        if (row["admission"] != ("blocked" if first else "available")
                or row["exit"] != NO_REPLY or row["new_reader_epoch"] != epoch
                or row["snapshot_reads"] != 4):
            raise ValueError("inconsistent process or reader outcome")
        catalog = set(payload)
        for phase, (state, candidates, pins) in zip(
            row["phases"], (("pending", [1, 2], [0] if first else [0, epoch]),
                            ("resolved", [epoch], [0, epoch]), ("resolved", [epoch], [])),
            strict=True,
        ):
            held = set().union(*(refs[n] for n in [*candidates, *pins]))
            removed = (catalog - held) if row["policy"] == "combined" or state == "resolved" else set()
            catalog -= removed
            wanted = {"state": state, "candidates": candidates, "pins": pins,
                      "removed": sorted(removed), "remaining": sorted(catalog),
                      "bytes_removed": sum(len(payload[n]) for n in removed)}
            if phase != wanted:
                raise ValueError("cleanup phase differs from the independent combined live set")
    first_pending = [r["phases"][0] for r in rows if r["order"] == "cleanup-first"
                     and r["policy"] == "combined"]
    if first_pending[0] != first_pending[1]:
        raise ValueError("pre-registration cleaner could distinguish writer outcomes")
    return {"trials": len(rows), "snapshot_reads": sum(r["snapshot_reads"] for r in rows),
            "contended_registrations": sum(r["admission"] == "blocked" for r in rows),
            "early_bytes": first_pending[0]["bytes_removed"],
            "older_only_files": len(refs[0] - (refs[1] | refs[2]))}


def binding() -> dict:
    sources = (Path(__file__).resolve(), ROOT / "scripts/reader_pins.py",
               ROOT / "scripts/publication_handoff.py", *CORPUS)
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sources}


def checked(value: dict) -> dict:
    if value["sources"] != binding() or summarize(value["runs"]) != value["summary"]:
        raise ValueError("stale combined reclamation evidence")
    return value["summary"]


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--writer", nargs=2, help=argparse.SUPPRESS)
    cli.add_argument("--newcomer", type=Path, help=argparse.SUPPRESS)
    cli.add_argument("--run", action="store_true")
    cli.add_argument("--record", type=Path)
    cli.add_argument("--table", type=Path)
    cli.add_argument("--emit-table", type=Path)
    args = cli.parse_args()
    if args.writer:
        writer(Path(args.writer[0]), args.writer[1])
        return
    if args.newcomer:
        newcomer(args.newcomer)
        return
    if args.run or args.record:
        sources = binding()
        rows = [experiment(a, b, c) for a in OUTCOMES for b in ORDERS for c in POLICIES]
        value = {"sources": sources, "runs": rows, "summary": summarize(rows)}
        checked(value)
        if args.record:
            with args.record.open("x", encoding="utf-8", newline="\n") as out:
                json.dump(value, out, indent=2, sort_keys=True)
                out.write("\n")
        elif value != json.loads(RECEIPT.read_bytes()):
            raise ValueError("combined replay differs from retained evidence")
    else:
        value = json.loads(RECEIPT.read_bytes())
    summary = checked(value)
    keys = {"CombinedTrials": "trials", "CombinedReads": "snapshot_reads",
            "CombinedContended": "contended_registrations", "CombinedEarlyBytes": "early_bytes",
            "CombinedOlderFiles": "older_only_files"}
    rendered = "% Generated from the retained combined reclamation observations.\n" + "".join(
        f"\\newcommand{{\\{name}}}{{{summary[key]:,}}}\n" for name, key in keys.items())
    if args.table and args.table.read_text(encoding="utf-8") != rendered:
        raise ValueError("paper combined counts differ from the retained evidence")
    if args.emit_table:
        with args.emit_table.open("x", encoding="utf-8", newline="\n") as out:
            out.write(rendered)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
