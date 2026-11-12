"""Compare frontier-only and exact strict-recovery witnesses in paired finite models."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from witness_examples import shared_chunk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from checker import analyze_model
from engine import census_template
from model import admit_trace
from oracle import evaluate_cut, exhaustive_model

MASKS = 1 << 15
CONTROL = ROOT / "results/census/graphs.jsonl"
RECEIPT = ROOT / "results/census/diagnostic-comparison.json"
FAMILIES = ("retired", "shared")


def candidates(model, report: dict) -> list[list[int]]:
    """Keep P and A witnesses; use only the last user for each failed D."""
    seeds = [[f["root"]] for f in report["publication_failures"]]
    seeds += [[f["frontier"], f["writer"]] for f in report["retirement_failures"]]
    seeds += [model.acknowledgements[f["ack_index"]]["anchors"]
              for f in report["acknowledgement_failures"]]
    return [sorted(model.closure_ids(ids)) for ids in seeds]


def compare(model, retained: dict | None = None) -> tuple[int | None, bool]:
    report = analyze_model(model, minimum=True)
    oracle = exhaustive_model(model, max_events=6)
    if (report["safe"], report["minimum_bad_cut"]) != (oracle["safe"], oracle["minimum_bad_cut"]):
        raise ValueError("exact diagnostic differs from materialized recovery")
    if retained is not None and any(oracle[k] != retained[k] for k in (
            "safe", "minimum_bad_cut", "legal_cuts", "bad_cuts")):
        raise ValueError("control differs from retained graph census")
    choices = candidates(model, report)
    if oracle["safe"]:
        if choices:
            raise ValueError("safe graph unexpectedly supplied a failure witness")
        return None, False
    if not choices:
        raise ValueError("unsafe graph has no frontier-only witness")
    baseline = min(choices, key=lambda cut: (len(cut), cut))
    if evaluate_cut(model, baseline)["safe"]:
        raise ValueError("frontier-only witness did not fail on recovery")
    delta = len(baseline) - len(oracle["minimum_bad_cut"])
    if delta < 0:
        raise ValueError("frontier-only witness is smaller than the exhaustive minimum")
    return delta, not (report["publication_failures"] or report["acknowledgement_failures"])


def inputs() -> dict[str, str]:
    files = ("scripts/diagnostic_census.py", "scripts/witness_examples.py", "src/engine.py",
             "src/model.py", "src/checker.py", "src/medium.py", "src/oracle.py",
             "results/census/graphs.jsonl")
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}


def count_rows() -> dict:
    control = {}
    with CONTROL.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row["family"] == "retired-free":
                mask = row["edge_mask"]
                if type(mask) is not int or mask in control:
                    raise ValueError("duplicate or invalid retained control mask")
                control[mask] = row
    if set(control) != set(range(MASKS)):
        raise ValueError("retained control is missing dependency masks")
    shared = shared_chunk("no-guard")
    counts = {
        name: Counter(dict.fromkeys(("graphs", "safe", "unsafe", "retirement_only", "larger", "larger_retirement_only"), 0))
        for name in FAMILIES
    }
    hist = {name: Counter() for name in FAMILIES}
    for mask in range(MASKS):
        retired = census_template("retired-free", mask)
        shared_case = dict(shared, events=[
            dict(e, deps=control_event["deps"])
            for e, control_event in zip(shared["events"], retired["events"], strict=True)
        ])
        for family, trace, baseline in (("retired", retired, control[mask]),
                                         ("shared", shared_case, None)):
            d, d_only = compare(admit_trace(trace), baseline)
            counts[family]["graphs"] += 1
            if d is None:
                counts[family]["safe"] += 1
                continue
            counts[family]["unsafe"] += 1
            counts[family]["retirement_only"] += int(d_only)
            counts[family]["larger"] += int(d > 0)
            counts[family]["larger_retirement_only"] += int(d > 0 and d_only)
            hist[family][str(d)] += 1
    return {name: dict(counts[name], excess_histogram=dict(hist[name]),
                       max_excess=max(map(int, hist[name]))) for name in FAMILIES}


def checked(receipt: dict) -> dict:
    if set(receipt) != {"inputs", "families"} or receipt["inputs"] != inputs():
        raise ValueError("diagnostic comparison receipt has stale inputs")
    families = receipt["families"]
    if set(families) != set(FAMILIES):
        raise ValueError("diagnostic comparison has unexpected families")
    for row in families.values():
        keys = {"graphs", "safe", "unsafe", "retirement_only", "larger",
                "larger_retirement_only", "max_excess", "excess_histogram"}
        if set(row) != keys or any(type(row[k]) is not int or row[k] < 0
                                  for k in keys - {"excess_histogram"}):
            raise ValueError("malformed diagnostic counts")
        hist = row["excess_histogram"]
        if (not hist or any(k not in set(map(str, range(7))) or type(v) is not int or v <= 0
                           for k, v in hist.items())
                or row["graphs"] != MASKS or row["graphs"] != row["safe"] + row["unsafe"]
                or row["unsafe"] != sum(hist.values())
                or row["larger"] != sum(v for k, v in hist.items() if k != "0")
                or row["max_excess"] != max(map(int, hist))
                or row["larger_retirement_only"] > min(row["larger"], row["retirement_only"])
                or row["retirement_only"] > row["unsafe"]):
            raise ValueError("inconsistent diagnostic counts")
    return families


def tex(families: dict) -> str:
    format_rows = ("graphs", "safe", "unsafe", "retirement_only", "larger",
                   "larger_retirement_only", "max_excess")
    fields = ("Graphs", "Safe", "Unsafe", "RetirementOnly", "Larger", "LargerRetirement", "MaxExcess")
    return "% Generated by the diagnostic census from the retained comparison.\n" + "".join(
        f"\\newcommand{{\\Diag{family.title()}{macro}}}{{{families[family][field]:,}}}\n"
        for family in FAMILIES for field, macro in zip(format_rows, fields, strict=True)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run", action="store_true", help="recompute the full paired comparison")
    group.add_argument("--record", type=Path, help="write a fresh paired comparison receipt")
    parser.add_argument("--table", type=Path)
    parser.add_argument("--emit-table", type=Path)
    args = parser.parse_args()
    if args.run or args.record:
        receipt = {"inputs": inputs(), "families": count_rows()}
        checked(receipt)
        if args.record:
            with args.record.open("x", encoding="utf-8", newline="\n") as out:
                json.dump(receipt, out, indent=2, sort_keys=True)
                out.write("\n")
        elif receipt != json.loads(RECEIPT.read_bytes()):
            raise ValueError("paired diagnostic replay differs from retained counts")
    else:
        receipt = json.loads(RECEIPT.read_bytes())
    families = checked(receipt)
    rendered = tex(families)
    if args.table and args.table.read_text(encoding="utf-8") != rendered:
        raise ValueError("manuscript counts differ from diagnostic evidence")
    if args.emit_table:
        with args.emit_table.open("x", encoding="utf-8", newline="\n") as out:
            out.write(rendered)
    print(json.dumps(families, sort_keys=True))


if __name__ == "__main__":
    main()
