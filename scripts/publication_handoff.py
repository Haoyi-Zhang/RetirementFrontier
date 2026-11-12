#!/usr/bin/env python3
"""Test reclamation when a worker's atomic publication has no delivered reply.

The publisher, cleaner and file format are owned fixtures. SQLite and the
filesystem are used normally; the publisher stops at application boundaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

from check_native_epochs import required_while_unresolved
from rocksdb_lifecycle import CORPUS, ROOT

RECEIPT = ROOT / "results/publication-handoff/observations.json"
MODES = ("DELETE", "WAL")
SHAPES = ("merge", "split")
BOUNDARIES = ("before-commit", "after-commit")
POLICIES = ("resolve-first", "candidate-guard")
NO_REPLY = 73
PAYLOAD = re.compile(r"[a-z][a-z0-9-]*\.bin")


def database(path: Path, mode: str | None = None) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        if mode is not None:
            if mode not in MODES or db.execute(f"PRAGMA journal_mode={mode}").fetchone()[0].upper() != mode:
                raise ValueError("journal mode not active")
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise ValueError("publication is not fully synchronized")
        return db
    except BaseException:
        db.close()
        raise


def fixture(shape: str) -> tuple[dict, dict[str, bytes], dict[str, bytes]]:
    if shape not in SHAPES:
        raise ValueError("unknown layout")
    alpha, beta, archive = (file.read_bytes() for file in CORPUS)
    left, right = alpha[:len(alpha) // 2], alpha[len(alpha) // 2:]
    parts = {"left.bin": left, "right.bin": right, "whole.bin": alpha,
             "shared.bin": beta, "archive.bin": archive}
    split = {"alpha": ["left.bin", "right.bin"], "beta": ["shared.bin"]}
    whole = {"alpha": ["whole.bin"], "beta": ["shared.bin"]}
    if shape == "split":
        parts["archive-extra.bin"] = beta[:len(beta) // 3]
    older, newer = (split, whole) if shape == "merge" else (whole, split)
    ticket = {"attempt": "maintenance", "client_prefix": 1,
              "views": {"0": older, "1": newer}}
    return ticket, parts, {"alpha": alpha, "beta": beta}


def durable_directory(path: Path) -> None:
    handle = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def put_file(path: Path, payload: bytes) -> None:
    with path.open("xb") as out:
        out.write(payload)
        out.flush()
        os.fsync(out.fileno())


def populate(root: Path, mode: str, ticket: dict, payloads: dict[str, bytes]) -> None:
    files = root / "segments"
    files.mkdir()
    for name, data in payloads.items():
        put_file(files / name, data)
    durable_directory(files)
    put_file(root / "pending.json", json.dumps(ticket, sort_keys=True).encode("ascii"))
    db = database(root / "catalog.db", mode)
    try:
        db.executescript("""
            CREATE TABLE selector (id INTEGER PRIMARY KEY CHECK (id=1), epoch INTEGER, prefix INTEGER);
            CREATE TABLE layouts (epoch INTEGER PRIMARY KEY, manifest TEXT NOT NULL);
            INSERT INTO selector VALUES (1, 0, 1);
        """)
        db.execute("INSERT INTO layouts VALUES (0, ?)", (json.dumps(ticket["views"]["0"], sort_keys=True),))
    finally:
        db.close()
    durable_directory(root)


def child(root: Path, mode: str, boundary: str) -> None:
    pending = json.loads((root / "pending.json").read_bytes())
    db = database(root / "catalog.db", mode)
    db.execute("BEGIN IMMEDIATE")
    # The layout and selector change together in the same SQLite transaction.
    db.execute("INSERT INTO layouts VALUES (1, ?)", (json.dumps(pending["views"]["1"], sort_keys=True),))
    db.execute("UPDATE selector SET epoch=1 WHERE id=1 AND epoch=0")
    if boundary == "after-commit":
        db.execute("COMMIT")
    elif boundary != "before-commit":
        raise ValueError("unknown interruption boundary")
    os._exit(NO_REPLY)


def references(view: dict) -> set[str]:
    if not isinstance(view, dict) or not view:
        raise ValueError("missing candidate layout")
    result = set()
    for pieces in view.values():
        if not isinstance(pieces, list) or not pieces or any(
            not isinstance(name, str) or PAYLOAD.fullmatch(name) is None for name in pieces
        ):
            raise ValueError("invalid segment name")
        result.update(pieces)
    return result


def inventory(root: Path) -> dict[str, int]:
    files = {}
    for path in (root / "segments").iterdir():
        if PAYLOAD.fullmatch(path.name) is None or path.is_symlink() or not path.is_file():
            raise ValueError("unexpected segment catalog member")
        files[path.name] = path.stat().st_size
    return files


def certify_pending(ticket: dict, files: dict[str, int]) -> list[str]:
    if ticket.get("attempt") != "maintenance" or ticket.get("client_prefix") != 1:
        raise ValueError("unrecognized pending operation")
    views = ticket.get("views", {})
    if set(views) != {"0", "1"}:
        raise ValueError("incomplete candidate set")
    held = required_while_unresolved(tuple(references(views[n]) for n in ("0", "1")))
    if not held <= files.keys():
        raise ValueError("candidate layout references an unavailable segment")
    plan = sorted(files.keys() - held)
    # Independent witness check: check each declared view without the union routine.
    remaining = files.keys() - set(plan)
    if any(not references(view) <= remaining for view in views.values()):
        raise ValueError("cleanup witness is not safe for all declared views")
    return plan


def clean(root: Path, plan: list[str]) -> None:
    for name in plan:
        if PAYLOAD.fullmatch(name) is None:
            raise ValueError("invalid cleanup target")
        (root / "segments" / name).unlink()
    durable_directory(root / "segments")


def reopen(root: Path, expected: dict[str, bytes]) -> tuple[int, set[str]]:
    db = database(root / "catalog.db")
    try:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")
        epoch, prefix = db.execute("SELECT epoch, prefix FROM selector WHERE id=1").fetchone()
        stored = db.execute("SELECT epoch, manifest FROM layouts ORDER BY epoch").fetchall()
        if epoch not in (0, 1) or prefix != 1 or [n for n, _ in stored] != list(range(epoch + 1)):
            raise ValueError("partial metadata transaction or unexpected client prefix")
        selected = json.loads(stored[-1][1])
        refs = references(selected)
        actual = {key: b"".join((root / "segments" / p).read_bytes() for p in pieces)
                  for key, pieces in selected.items()}
        if actual != expected:
            raise ValueError("native recovery returned different object bytes")
        return epoch, refs
    finally:
        db.close()


def experiment(mode: str, shape: str, boundary: str, policy: str) -> dict:
    ticket, data, expected = fixture(shape)
    with tempfile.TemporaryDirectory(prefix="rf-publication-handoff-") as directory:
        root = Path(directory)
        populate(root, mode, ticket, data)
        run = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--child",
                              str(root), mode, boundary], capture_output=True, timeout=30)
        if run.returncode != NO_REPLY or run.stdout or run.stderr:
            raise ValueError("worker did not stop without a completion reply")
        # Pre-resolution policy observes only this handoff and the segment
        # catalog. It does not read SQLite, its journal, or the injected boundary.
        pending = json.loads((root / "pending.json").read_bytes())
        files = inventory(root)
        visible = {"exit_code": run.returncode, "reply_received": False,
                   "pending": pending, "inventory": files}
        pre = certify_pending(pending, files) if policy == "candidate-guard" else []
        if pre:
            clean(root, pre)
        epoch, actual = reopen(root, expected)
        if epoch != (1 if boundary == "after-commit" else 0):
            raise ValueError("native publication outcome contradicts the boundary")
        if actual != references(pending["views"][str(epoch)]):
            raise ValueError("recovered references differ from the declared layout")
        after = sorted(inventory(root).keys() - actual)
        clean(root, after)
        if reopen(root, expected) != (epoch, actual):
            raise ValueError("cleanup changed the selected recovery view")
        row = {"mode": mode, "shape": shape, "boundary": boundary, "policy": policy,
               "visible": visible, "selected_epoch": epoch, "selected_files": sorted(actual),
               "before_resolution": pre, "after_resolution": after,
               "bytes_before_resolution": sum(files[n] for n in pre),
               "bytes_total": sum(files[n] for n in pre + after),
               "verified_objects": len(expected), "native_reopens": 2}
    return row


def summarize(rows: list[dict]) -> dict:
    matrix = [(m, s, b, p) for m in MODES for s in SHAPES
              for b in BOUNDARIES for p in POLICIES]
    if [tuple(r[k] for k in ("mode", "shape", "boundary", "policy")) for r in rows] != matrix:
        raise ValueError("incomplete publication handoff campaign")
    pairs = 0
    guessed_old_bad = guessed_new_bad = 0
    guard_early = guard_bytes = 0
    per_shape = {}
    for mode in MODES:
        for shape in SHAPES:
            block = [r for r in rows if (r["mode"], r["shape"]) == (mode, shape)]
            visible = block[0]["visible"]
            if any(r["visible"] != visible for r in block):
                raise ValueError("handoff exposed the transaction outcome")
            pending, files = visible["pending"], visible["inventory"]
            expected_ticket, data, _ = fixture(shape)
            if (visible["exit_code"] != NO_REPLY or visible["reply_received"] is not False
                    or pending != expected_ticket or files != {k: len(v) for k, v in data.items()}):
                raise ValueError("handoff does not match the declared fixture")
            before, after = (references(pending["views"][i]) for i in ("0", "1"))
            exact = certify_pending(pending, files)
            per_shape[shape] = {"files": len(exact), "bytes": sum(files[n] for n in exact)}
            for boundary, actual in zip(BOUNDARIES, (before, after), strict=True):
                variants = [r for r in block if r["boundary"] == boundary]
                if not all(r["selected_epoch"] == int(boundary == "after-commit")
                           and set(r["selected_files"]) == actual and r["native_reopens"] == 2
                           and r["verified_objects"] == 2 for r in variants):
                    raise ValueError("inconsistent native recovery evidence")
                guessed_old_bad += bool(actual - before)
                guessed_new_bad += bool(actual - after)
                for r in variants:
                    wanted = exact if r["policy"] == "candidate-guard" else []
                    later = sorted(files.keys() - actual - set(wanted))
                    if r["before_resolution"] != wanted or r["after_resolution"] != later:
                        raise ValueError("cleanup order or file accounting differs")
                    if (r["bytes_before_resolution"] != sum(files[n] for n in wanted)
                            or r["bytes_total"] != sum(files[n] for n in files.keys() - actual)):
                        raise ValueError("incorrect native byte counts")
                    if r["policy"] == "candidate-guard":
                        guard_early += len(wanted)
                        guard_bytes += r["bytes_before_resolution"]
            pairs += 1
    return {"runs": len(rows), "matched_pairs": pairs, "native_reopens": 2 * len(rows),
            "guard_early_files": guard_early, "guard_early_bytes": guard_bytes,
            "assume_before_unsafe": guessed_old_bad, "assume_after_unsafe": guessed_new_bad,
            "per_shape": per_shape}


def binding() -> dict:
    sources = (Path(__file__).resolve(), ROOT / "scripts/check_native_epochs.py",
               ROOT / "scripts/rocksdb_lifecycle.py", *CORPUS)
    return {f.relative_to(ROOT).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sources}


def checked(value: dict) -> dict:
    if value["sources"] != binding() or summarize(value["runs"]) != value["summary"]:
        raise ValueError("stale publication handoff evidence")
    return value["summary"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", nargs=3, help=argparse.SUPPRESS)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--record", type=Path)
    parser.add_argument("--emit-table", type=Path)
    parser.add_argument("--table", type=Path)
    args = parser.parse_args()
    if args.child:
        root, mode, boundary = args.child
        child(Path(root), mode, boundary)
        return
    if args.run or args.record:
        before = binding()
        rows = [experiment(m, s, b, p) for m in MODES for s in SHAPES
                for b in BOUNDARIES for p in POLICIES]
        value = {"sources": before, "runs": rows, "summary": summarize(rows)}
        checked(value)
        if args.record:
            with args.record.open("x", encoding="utf-8", newline="\n") as out:
                json.dump(value, out, indent=2, sort_keys=True)
                out.write("\n")
        elif value != json.loads(RECEIPT.read_bytes()):
            raise ValueError("publication handoff rerun differs from the retained evidence")
    else:
        value = json.loads(RECEIPT.read_bytes())
    summary = checked(value)
    names = {"HandoffRuns": "runs", "HandoffPairs": "matched_pairs",
             "HandoffReopens": "native_reopens", "HandoffEarlyFiles": "guard_early_files",
             "HandoffEarlyBytes": "guard_early_bytes",
             "HandoffBeforeUnsafe": "assume_before_unsafe",
             "HandoffAfterUnsafe": "assume_after_unsafe"}
    fields = {macro: summary[key] for macro, key in names.items()}
    for shape, scores in summary["per_shape"].items():
        for key, score in scores.items():
            fields[f"Handoff{shape.title()}{key.title()}"] = score
    rendered = "% Generated from the retained publication-handoff observations.\n" + "".join(
        f"\\newcommand{{\\{macro}}}{{{score:,}}}\n" for macro, score in sorted(fields.items()))
    if args.table and args.table.read_text(encoding="utf-8") != rendered:
        raise ValueError("paper handoff counts differ from the retained evidence")
    if args.emit_table:
        with args.emit_table.open("x", encoding="utf-8", newline="\n") as out:
            out.write(rendered)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
