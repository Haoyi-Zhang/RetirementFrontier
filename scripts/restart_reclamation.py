#!/usr/bin/env python3
"""Replay an interrupted, application-owned reclamation pass."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import combined_reclamation as combined
from publication_handoff import NO_REPLY
from reader_pins import (CORPUS, ROOT, database, durable_directory, fixture, gate,
                         initialize, inventory, launch, publish, references,
                         request, selected, verify_bytes, write_payloads)

RECEIPT = ROOT / "results/restart-reclamation/observations.json"
INTERRUPTED = 76
CUTS = ("before-unlink", "partial-unsynced", "partial-synced", "after-sync")
ORDERS = ("keep-oldest", "release-oldest")
STAGES = (*CUTS, "uninterrupted")


def material():
    views, payload, objects = fixture()
    extras = {"spare-alpha.bin": objects["alpha"], "spare-beta.bin": objects["beta"]}
    return views, payload | extras, extras, objects


def worker(root: Path, cut: str) -> None:
    """Execute one actual pass. No outcome hint or previous plan is supplied."""
    if cut not in STAGES:
        raise ValueError("unknown interruption point")
    with gate(root):
        ticket, pins, _, targets = combined.assess_locked(root)
        if cut == "before-unlink":
            os._exit(INTERRUPTED)
        for i, name in enumerate(targets):
            (root / "segments" / name).unlink()
            if i == 0 and cut in ("partial-unsynced", "partial-synced"):
                if cut == "partial-synced":
                    durable_directory(root / "segments")
                os._exit(INTERRUPTED)
        durable_directory(root / "segments")
        if cut == "after-sync":
            os._exit(INTERRUPTED)
        print(json.dumps({"state": ticket["state"], "pins": pins, "removed": targets}))


def run_worker(root: Path, cut: str) -> dict | None:
    proc = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                           "--worker", str(root), cut], capture_output=True, timeout=30)
    if cut == "uninterrupted" and proc.returncode == 0 and not proc.stderr:
        return json.loads(proc.stdout)
    if cut in CUTS and proc.returncode == INTERRUPTED and not (proc.stdout or proc.stderr):
        return None
    raise ValueError(f"unexpected cleanup process outcome at {cut}: {proc.returncode}, {proc.stderr!r}")


def control(root: Path) -> tuple[bytes, dict[str, bytes]]:
    return ((root / "handoff.json").read_bytes(),
            {p.name: p.read_bytes() for p in (root / "pins").iterdir()})


def experiment(outcome: str, order: str, cut: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="rf-restart-") as tmp, ExitStack() as readers:
        root = Path(tmp)
        _, _, extras, objects = material()
        initialize(root)
        oldest = readers.enter_context(launch(root, "first", 0))
        publish(root, 1)
        combined.prepare(root)
        write_payloads(root, set(extras), extras)
        writer = subprocess.run([sys.executable, "-B", str(Path(combined.__file__)),
                                 "--writer", str(root), outcome], capture_output=True, timeout=30)
        if writer.returncode != NO_REPLY or writer.stdout or writer.stderr:
            raise ValueError("publisher did not exit silently at the declared boundary")
        epoch = 1 + (outcome == "after-commit")
        recent = readers.enter_context(launch(root, "second", epoch))
        initial = inventory(root)
        metadata = control(root)
        first = run_worker(root, cut)
        observed = inventory(root)
        if control(root) != metadata:
            raise ValueError("cleanup attempt changed protection metadata")
        reads = 0

        def check_readers(include_oldest: bool) -> None:
            nonlocal reads
            if include_oldest:
                request(oldest, "verify", "verified 0")
                reads += 1
            request(recent, "verify", f"verified {epoch}")
            reads += 1

        check_readers(True)
        keep_oldest = order == "keep-oldest"
        if not keep_oldest:
            combined.release(oldest)
        metadata = control(root)
        retry = run_worker(root, "uninterrupted")
        after_retry = inventory(root)
        if control(root) != metadata:
            raise ValueError("retry changed protection metadata")
        check_readers(keep_oldest)
        with gate(root):
            resolved_epoch = combined.resolve_locked(root)
        after_resolve = run_worker(root, "uninterrupted")
        check_readers(keep_oldest)
        if keep_oldest:
            combined.release(oldest)
        combined.release(recent)
        drain = run_worker(root, "uninterrupted")
        no_op = run_worker(root, "uninterrupted")
        with gate(root), closing(database(root / "catalog.db")) as db:
            final_epoch, final_view = selected(db)
            if final_epoch != resolved_epoch:
                raise ValueError("the selected layout changed during cleanup")
            verify_bytes(root, final_view, objects)
        return {"outcome": outcome, "order": order, "cut": cut, "epoch": final_epoch,
                "initial": sorted(initial), "after_interruption": sorted(observed),
                "after_retry": sorted(after_retry), "final": sorted(inventory(root)),
                "first": first, "retry": retry, "after_resolve": after_resolve,
                "drain": drain, "no_op": no_op, "snapshot_reads": reads}


def summarize(rows: list[dict]) -> dict:
    views, payload, _, _ = material()
    refs = [references(view) for view in views]
    matrix = [(outcome, order, cut) for outcome in combined.OUTCOMES
              for order in ORDERS for cut in STAGES]
    if [tuple(row[k] for k in ("outcome", "order", "cut")) for row in rows] != matrix:
        raise ValueError("incomplete restart trial matrix")
    initial = set(payload)
    garbage = sorted(initial - set().union(*refs))
    new_after_release = refs[0] - (refs[1] | refs[2])
    if len(garbage) < 2 or not new_after_release:
        raise ValueError("fixture cannot distinguish partial cleanup and changed pins")
    for (outcome, order, cut), row in zip(matrix, rows, strict=True):
        epoch = 1 + (outcome == "after-commit")
        keep = order == "keep-oldest"
        live = [0, epoch] if keep else [epoch]
        first_count = {"before-unlink": 0, "partial-unsynced": 1, "partial-synced": 1}.get(
            cut, len(garbage))
        remaining = initial - set(garbage[:first_count])
        eligible_after = set(garbage) | (set() if keep else new_after_release)
        pending_end = initial - eligible_after
        resolved_end = set().union(*(refs[n] for n in live))

        def step(state: str, pins: list[int], removes) -> dict:
            return {"state": state, "pins": pins, "removed": sorted(removes)}

        expected = {"epoch": epoch, "initial": sorted(initial), "after_interruption": sorted(remaining),
                    "after_retry": sorted(pending_end), "final": sorted(refs[epoch]),
                    "first": (step("pending", [0, epoch], garbage) if cut == "uninterrupted" else None),
                    "retry": step("pending", live, remaining - pending_end),
                    "after_resolve": step("resolved", live, pending_end - resolved_end),
                    "drain": step("resolved", [], resolved_end - refs[epoch]),
                    "no_op": step("resolved", [], []), "snapshot_reads": 6 if keep else 4}
        if set(row) != {*expected, "outcome", "order", "cut"} or any(
                row[key] != value for key, value in expected.items()):
            raise ValueError(f"restart result disagrees with inventory oracle: {outcome}/{order}/{cut}")
    return {"trials": len(rows), "interruptions": sum(r["cut"] in CUTS for r in rows),
            "snapshot_reads": sum(r["snapshot_reads"] for r in rows),
            "new_files_after_release": len(new_after_release),
            "new_bytes_after_release": sum(len(payload[n]) for n in new_after_release)}


def binding() -> dict:
    sources = (Path(__file__).resolve(), Path(combined.__file__),
               ROOT / "scripts/reader_pins.py", ROOT / "scripts/publication_handoff.py", *CORPUS)
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}


def checked(value: dict) -> dict:
    if value["sources"] != binding() or summarize(value["runs"]) != value["summary"]:
        raise ValueError("stale restart-reclamation evidence")
    return value["summary"]


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--worker", nargs=2, help=argparse.SUPPRESS)
    cli.add_argument("--run", action="store_true")
    cli.add_argument("--record", type=Path)
    cli.add_argument("--table", type=Path)
    cli.add_argument("--emit-table", type=Path)
    args = cli.parse_args()
    if args.worker:
        worker(Path(args.worker[0]), args.worker[1])
        return
    if args.run or args.record:
        sources = binding()
        rows = [experiment(o, r, c) for o in combined.OUTCOMES for r in ORDERS for c in STAGES]
        value = {"sources": sources, "runs": rows, "summary": summarize(rows)}
        checked(value)
        if args.record:
            with args.record.open("x", encoding="utf-8", newline="\n") as out:
                json.dump(value, out, indent=2, sort_keys=True)
                out.write("\n")
        elif value != json.loads(RECEIPT.read_bytes()):
            raise ValueError("restart replay differs from retained evidence")
    else:
        value = json.loads(RECEIPT.read_bytes())
    summary = checked(value)
    keys = {"RestartTrials": "trials", "RestartStops": "interruptions", "RestartReads": "snapshot_reads",
            "RestartNewFiles": "new_files_after_release", "RestartNewBytes": "new_bytes_after_release"}
    rendered = "% Generated from the retained restart observations.\n" + "".join(
        f"\\newcommand{{\\{name}}}{{{summary[key]:,}}}\n" for name, key in keys.items())
    if args.table and args.table.read_text(encoding="utf-8") != rendered:
        raise ValueError("paper restart counts differ from the retained evidence")
    if args.emit_table:
        with args.emit_table.open("x", encoding="utf-8", newline="\n") as out:
            out.write(rendered)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
