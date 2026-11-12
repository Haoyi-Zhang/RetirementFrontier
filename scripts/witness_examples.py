"""Check the manuscript's finite witnesses without starting a storage engine."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from check_native_epochs import RESULT, projection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from checker import analyze_model, pair_analyze_model
from model import admit_trace, canonical_manifest, make_ref
from oracle import evaluate_cut, exhaustive_model
from verifier import verify_certificate


def shared_chunk(mode: str) -> dict:
    """Two successive roots share one incarnation; only a third retires it."""
    if mode not in ("no-guard", "force-user", "force-retirement"):
        raise ValueError("unknown shared-chunk variant")
    value = b"a"
    chunk = make_ref(0, 1, value)
    events = [dict(id=0, kind="data", **chunk, role="chunk", data_hex="61", deps=[])]
    operations = [dict(op="put", name=name, chunks_hex=["61"]) for name in ("x", "y")]

    def publish(prefix: int, slot: int, names: tuple[str, ...], preds: list[int]) -> None:
        view = {name: [chunk] for name in names}
        encoded = canonical_manifest(view)
        manifest = make_ref(slot, 1, encoded)
        i = len(events)
        events.append(dict(id=i, kind="data", **manifest, role="manifest",
                           data_hex=encoded.hex(), deps=[]))
        events.append(dict(id=i + 1, kind="root", prefix=prefix, manifest_ref=manifest,
                           namespace=view, objects_hex={name: "61" for name in names},
                           deps=sorted([*preds, i])))

    publish(1, 1, ("x",), [0])
    publish(2, 2, ("x", "y"), [2])
    if mode == "force-retirement":
        operations.extend(dict(op="delete", name=name) for name in ("x", "y"))
        publish(4, 3, (), [4])
    predecessor = {"no-guard": 0, "force-user": 4, "force-retirement": 6}[mode]
    events.append(dict(id=len(events), kind="free", slot=0, generation=1, deps=[predecessor]))
    return dict(schema="retirement-frontier-trace-v1", events=events, operations=operations,
                acknowledgements=[])


def cross_check(model, exact_limit: int = 12) -> dict:
    report = analyze_model(model, minimum=True)
    pair = pair_analyze_model(model, minimum=True)
    oracle = exhaustive_model(model, max_events=exact_limit)
    outcome = report["safe"], report["minimum_bad_cut"]
    if any((other["safe"], other["minimum_bad_cut"]) != outcome for other in (pair, oracle)):
        raise ValueError("frontier, pairwise decision and materialized recovery disagree")
    if report["safe"] and not verify_certificate(model.trace, report["certificate"])["accepted"]:
        raise ValueError("separate consumer rejected the positive witness")
    return report


def shared_comparison() -> dict:
    original, forced, repaired = (admit_trace(shared_chunk(mode))
                                  for mode in ("no-guard", "force-user", "force-retirement"))
    first, second, third = map(cross_check, (original, forced, repaired))
    failure, = first["retirement_failures"]
    another_failure, = second["retirement_failures"]
    frontier_cut = sorted(original.closure_ids([failure["frontier"], failure["writer"]]))
    minimum = first["minimum_bad_cut"]
    # Materialized images also identify the selected root and tombstone;
    # the scalar failure alone does not supply either crash image.
    for model, cut, root, writer in ((original, minimum, 2, 5),
                                     (original, frontier_cut, 4, 5),
                                     (forced, second["minimum_bad_cut"], 4, 5)):
        result = evaluate_cut(model, cut)
        if result["safe"] or result["reason"] != "recovery" or result["outcome"]["root_event"] != root:
            raise ValueError("shared-chunk witness does not fail at the claimed root")
        if model.events[writer]["kind"] != "free" or writer not in cut:
            raise ValueError("witness did not retain the destructive event")
    if (failure != dict(writer=5, frontier=4, guard=-1)
            or another_failure != dict(writer=5, frontier=4, guard=4)
            or frontier_cut != [0, 1, 2, 3, 4, 5] or minimum != [0, 1, 2, 5]
            or second["minimum_bad_cut"] != frontier_cut
            or third["certificate"]["retirement"] != [dict(writer=7, guard_root=6)]):
        raise ValueError("shared-chunk example differs from the manuscript")
    return {"frontier_cut": frontier_cut, "minimum_cut": minimum,
            "selected_roots": [4, 2], "forced_user_cut": second["minimum_bad_cut"],
            "retiring_guard": 6, "repaired_free": 7}


def native_comparison() -> dict:
    """Offline model projection of an already decoded edit, not a native replay."""
    case, = (row for row in json.loads(RESULT.read_text(encoding="utf-8"))["cases"]
             if row["scope"] == "range")
    before, after = set(case["before"]["files"]), set(case["after"]["files"])
    rows = {}
    for profile, publish, retire, expected_missing, expected_view in (
        ("unguarded-retirement", True, False, ["000009.sst"], "epoch:before"),
        ("unguarded-publication", False, True, ["000022.sst"], "epoch:after"),
    ):
        model, files, unlinks, old, new, labels = projection(before, after, publish, retire)
        report = cross_check(model)
        cut = set(report["minimum_bad_cut"])
        view, names = (new, after) if new in cut else (old, before)
        missing = sorted(name for name in names if files[name] not in cut or unlinks.get(name) in cut)
        observed = dict(cut=[labels[i] for i in sorted(cut)], view=labels[view], missing=missing)
        if (observed != case["outcomes"][profile]["minimum"]
                or missing != expected_missing or labels[view] != expected_view):
            raise ValueError("range-compaction witness differs from retained projection")
        rows[profile] = dict(view=labels[view], missing=missing, durable_events=len(cut))
    return rows


if __name__ == "__main__":
    print(json.dumps(dict(shared=shared_comparison(), range_projection=native_comparison()),
                     indent=2, sort_keys=True))
