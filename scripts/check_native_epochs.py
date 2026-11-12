#!/usr/bin/env python3
"""Check a bounded file-availability projection of native RocksDB edits."""
from __future__ import annotations

import argparse
import hashlib
import json
from itertools import combinations
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from rocksdb_lifecycle import WORKLOADS, RECEIPT as NATIVE, ROOT, checked_receipt, execute

sys.path.insert(0, str(ROOT / "src"))
from checker import analyze_model
from medium import materialize, recover
from model import admit_trace, canonical_manifest, make_ref
from verifier import verify_certificate

RESULT = ROOT / "results/rocksdb-lifecycle/epochs.json"
PROFILES = {
    "ordered": (True, True),
    "unguarded-retirement": (True, False),
    "unguarded-publication": (False, True),
    "both-unguarded": (False, False),
}


def decode(output: str) -> tuple[list[dict], dict[str, int], int]:
    """Select only file membership and sequence fields from a stock ldb dump."""
    prefix, sep, tail = output.partition('--------------- Column family "default"  (ID 0)')
    if not sep or "Column family" in tail:
        raise ValueError("expected a single default column family")
    edits = []
    for body in re.findall(r"(?ms)^VersionEdit \{\n(.*?)^\}", prefix):
        if re.findall(r"(?m)^  ColumnFamily: (\d+)$", body) != ["0"]:
            raise ValueError("unexpected native version edit")
        if "AtomicGroup" in body or "RemainingEntries" in body:
            raise ValueError("atomic multi-edit groups are outside the adapter")
        edits.append({
            "add": {f"{int(i):06d}.sst": int(n) for i, n in re.findall(
                r"(?m)^  AddFile: \d+ (\d+) (\d+) ", body)},
            "remove": sorted(f"{int(i):06d}.sst" for i in re.findall(
                r"(?m)^  DeleteFile: \d+ (\d+)$", body)),
            "sequence": [int(n) for n in re.findall(r"(?m)^  LastSeq: (\d+)$", body)],
        })
    final = {f"{int(i):06d}.sst": int(n) for i, n in re.findall(
        r"(?m)^ +(\d+):(\d+)\[", tail)}
    sequence = re.findall(r"(?m)^next_file_number \d+ last_sequence (\d+) ", tail)
    if not edits or not final or len(sequence) != 1:
        raise ValueError("incomplete native manifest decode")
    return edits, final, int(sequence[0])


def projection(before: set[str], after: set[str], publication: bool, retirement: bool):
    """Lower file-presence obligations, not SST contents, to the common model."""
    slots = {name: i for i, name in enumerate(sorted(before | after))}
    events = []
    by_file = {}
    labels = []

    def add(label, kind, **values):
        i = len(events)
        events.append(dict(id=i, kind=kind, **values))
        labels.append(label)
        return i

    def file(name):
        by_file[name] = add(f"available:{name}", "data", **make_ref(slots[name], 1, b""),
                            role="chunk", data_hex="", deps=[])

    def root(label, names, require, predecessor, slot):
        namespace = {"table-presence": [make_ref(slots[n], 1, b"") for n in sorted(names)]}
        encoded = canonical_manifest(namespace)
        ref = make_ref(slot, 1, encoded)
        manifest = add(f"descriptor:{label}", "data", **ref, role="manifest",
                       data_hex=encoded.hex(), deps=[])
        return add(label, "root", prefix=1, manifest_ref=ref, namespace=namespace,
                   objects_hex={"table-presence": ""}, deps=sorted(
                       [manifest, *predecessor, *(by_file[n] for n in require)]))

    for name in sorted(before):
        file(name)
    old = root("epoch:before", before, before, [], len(slots))
    for name in sorted(after - before):
        file(name)
    new = root("epoch:after", after, after if publication else before & after,
               [old], len(slots) + 1)
    removals = {name: add(f"retire:{name}", "free", slot=slots[name], generation=1,
                         deps=[new if retirement else old]) for name in sorted(before - after)}
    model = admit_trace(dict(schema="retirement-frontier-trace-v1", events=events,
                             operations=[dict(op="put", name="table-presence", chunks_hex=[""])],
                             acknowledgements=[]))
    return model, by_file, removals, old, new, labels


def compare(before: set[str], after: set[str], publish: bool, retire: bool) -> dict:
    model, files, removals, old, new, labels = projection(before, after, publish, retire)
    outcome = analyze_model(model, minimum=True)
    violations = []
    legal = 0
    for bits in range(1 << model.n):
        cut = tuple(i for i in range(model.n) if bits & (1 << i))
        if not model.is_legal_cut(cut):
            continue
        legal += 1
        view, needed = (new, after) if new in cut else (old, before) if old in cut else (None, set())
        missing = sorted(name for name in needed if files[name] not in cut or removals.get(name) in cut)
        actual = recover(materialize(model, cut))
        if actual["ok"] != (not missing) or actual["root_event"] != view:
            raise ValueError("native-reference oracle disagrees with projected recovery")
        if missing:
            violations.append((cut, view, missing))
    if bool(violations) == outcome["safe"]:
        raise ValueError("frontier decision disagrees with the independent oracle")
    minimum = min(violations, key=lambda row: (len(row[0]), row[0])) if violations else None
    if minimum and list(minimum[0]) != outcome["minimum_bad_cut"]:
        raise ValueError("minimum witness disagrees with the independent oracle")
    guards = []
    if outcome["safe"]:
        certificate = outcome["certificate"]
        if not verify_certificate(model.trace, certificate)["accepted"]:
            raise ValueError("separate certificate verifier rejected a safe projection")
        guards = [[labels[row["writer"]], labels[row["guard_root"]]]
                  for row in certificate["retirement"]]
    return {
        "legal_cuts": legal, "bad_cuts": len(violations), "certificate_guards": guards,
        "minimum": None if minimum is None else {
            "cut": [labels[i] for i in minimum[0]], "view": labels[minimum[1]], "missing": minimum[2],
        },
    }


def required_while_unresolved(candidates: tuple[set[str], ...]) -> set[str]:
    """Conservative hold set when the durable publication is not yet known."""
    if not candidates:
        raise ValueError("reclamation requires at least one candidate view")
    return set().union(*candidates)


def check_uncertainty(before: set[str], after: set[str]) -> dict:
    """Check the separate abstract cleanup decision after output creation."""
    catalog = sorted(before | after)
    removed_sets = [set(items) for n in range(len(catalog) + 1)
                    for items in combinations(catalog, n)]
    false_accepts = {"candidate-union": 0, "assume-before": 0, "assume-after": 0}
    known_safe = {}
    for state, candidates in (("before-confirmed", (before,)),
                              ("unresolved", (before, after)),
                              ("after-confirmed", (after,))):
        required = required_while_unresolved(candidates)
        accepted = 0
        for removed in removed_sets:
            keep = set(catalog) - removed
            oracle = all(all(name in keep for name in view) for view in candidates)
            decision = not (removed & required)
            if decision != oracle:
                raise ValueError("cleanup guard disagrees with candidate-by-candidate recovery")
            accepted += decision
            if state == "unresolved" and not oracle:
                for name, guard in (("candidate-union", required),
                                    ("assume-before", before), ("assume-after", after)):
                    false_accepts[name] += not (removed & guard)
        known_safe[state] = accepted
    return {"subsets_per_state": len(removed_sets), "safe_subsets": known_safe,
            "unsafe_subsets_accepted_while_unresolved": false_accepts}


def validate(cases: list[dict], native: dict) -> list[dict]:
    if [(c["scope"], c["batch_size"]) for c in cases] != list(WORKLOADS):
        raise ValueError("incomplete native edit campaign")
    result = []
    for case, observed in zip(cases, checked_receipt(native), strict=True):
        before, after = observed["observations"][2:4]
        b, a = case["before"], case["after"]
        if (before["live"], before["sequence"], before["manifest_bytes"]) != (
            b["files"], b["sequence"], b["bytes"]
        ) or (after["live"], after["sequence"], after["manifest_bytes"]) != (
            a["files"], a["sequence"], a["bytes"]
        ):
            raise ValueError("independent decoder disagrees with native engine view")
        transition = case["edits"]
        changed = [e for e in transition if e["add"] or e["remove"]]
        if len(changed) != 1 or b["sequence"] != a["sequence"]:
            raise ValueError("adapter requires one membership edit at the same client sequence")
        delta = changed[0]
        if (set(delta["remove"]) != b["files"].keys() - a["files"].keys()
                or delta["add"] != {k: a["files"][k] for k in a["files"].keys() - b["files"].keys()}):
            raise ValueError("membership edit does not explain native views")
        before_set, after_set = set(b["files"]), set(a["files"])
        outcomes = {key: compare(before_set, after_set, *values)
                    for key, values in PROFILES.items()}
        result.append({**case, "outcomes": outcomes,
                       "uncertainty": check_uncertainty(before_set, after_set)})
    return result


def bindings():
    names = ["scripts/check_native_epochs.py", "src/model.py", "src/medium.py",
             "src/checker.py", "src/verifier.py"]
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def campaign() -> tuple[dict, dict]:
    sources = bindings()
    with tempfile.TemporaryDirectory(prefix="native-epochs-") as work:
        previous = os.environ.get("RETIREMENT_MANIFEST_CAPTURE")
        os.environ["RETIREMENT_MANIFEST_CAPTURE"] = work
        try:
            native = execute()
        finally:
            if previous is None:
                del os.environ["RETIREMENT_MANIFEST_CAPTURE"]
            else:
                os.environ["RETIREMENT_MANIFEST_CAPTURE"] = previous
        cases = []
        for scope, batch in WORKLOADS:
            folder = Path(work) / f"database-{batch}"
            before, after = (folder / f"{n}.manifest" for n in ("before-compaction", "after-live"))
            if not after.read_bytes().startswith(before.read_bytes()):
                raise ValueError("native metadata did not append to the same manifest")
            def read(path):
                out = subprocess.check_output(["ldb", "manifest_dump", "--verbose",
                                               f"--path={path}"], text=True, timeout=30)
                edits, files, seq = decode(out)
                return edits, {"files": files, "sequence": seq, "bytes": path.stat().st_size}
            old, b = read(before)
            new, a = read(after)
            if new[:len(old)] != old:
                raise ValueError("native decoded history did not extend its prefix")
            cases.append({"scope": scope, "batch_size": batch,
                          "before": b, "after": a, "edits": new[len(old):]})
    if sources != bindings():
        raise ValueError("projection sources changed during execution")
    return native, {"sources": sources, "cases": validate(cases, native)}


def checked(result: dict, native: dict) -> dict:
    if result["sources"] != bindings() or validate(result["cases"], native) != result["cases"]:
        raise ValueError("stale native epoch evidence")
    cases = result["cases"]
    ranged = [c for c in cases if c["scope"] == "range"]
    uncertainty = {name: sum(c["uncertainty"]["unsafe_subsets_accepted_while_unresolved"][name]
                             for c in cases)
                   for name in ("candidate-union", "assume-before", "assume-after")}
    return {"cases": len(cases), "profiles": len(PROFILES),
            "cuts": sum(o["legal_cuts"] for c in cases for o in c["outcomes"].values()),
            "bad": [sum(c["outcomes"][name]["bad_cuts"] for c in cases) for name in PROFILES],
            "range_ordered_cuts": sum(c["outcomes"]["ordered"]["legal_cuts"] for c in ranged),
            "uncertainty_subsets": sum(c["uncertainty"]["subsets_per_state"] for c in cases),
            "uncertainty_false_accepts": uncertainty}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--record", type=Path, help="native capture into a fresh staging directory")
    cli.add_argument("--run", action="store_true", help="rerun and compare against retained evidence")
    args = cli.parse_args()
    if args.record or args.run:
        native, result = campaign()
        if args.record:
            for name, payload in (("observations.json", native), ("epochs.json", result)):
                with (args.record / name).open("x", encoding="utf-8", newline="\n") as out:
                    json.dump(payload, out, indent=2, sort_keys=True)
                    out.write("\n")
        elif (native != json.loads(NATIVE.read_text(encoding="utf-8"))
              or result != json.loads(RESULT.read_text(encoding="utf-8"))):
            raise ValueError("new native capture differs from retained evidence")
    else:
        native = json.loads(NATIVE.read_text(encoding="utf-8"))
        result = json.loads(RESULT.read_text(encoding="utf-8"))
    print(json.dumps(checked(result, native)))
