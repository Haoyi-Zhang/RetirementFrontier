"""Paired restores on fresh RocksDB databases with two public-data workloads."""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "results/rocksdb-lifecycle/checkpoint-diversity.json"
SOURCE = Path(__file__).with_suffix(".cc")
DATA = {
    "AirPassengers": ("bdb98adbd418a6de6842a742e0602f363c3b841c26677e7e61a1e055e9509bd8", 144),
    "Nile": ("d0452bea38c61e796a4eeb950bf91d20fb5c7f13d5822eadf5990fe54f9c8d07", 100),
}
DESIGN = (("AirPassengers", "append"), ("Nile", "update-delete"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def operations(name, shape, data):
    fingerprint, count = DATA[name]
    if digest(data) != fingerprint:
        raise ValueError(f"source bytes changed: {name}")
    rows = list(csv.DictReader(io.StringIO(data.decode("ascii"))))
    if len(rows) != count or any(int(row["rownames"]) != i for i,row in enumerate(rows, 1)):
        raise ValueError(f"unexpected row sequence: {name}")
    out = []
    for half, prefix in enumerate(("a", "y")):
        split = count // 4
        for batch in range(2):
            start = (half * 2 + batch) * split
            for i,row in enumerate(rows[start:start+split]):
                # Two insert-only streams interleave in key order. Disjoint
                # batches can be moved between levels without rewriting SSTs.
                index = 2*i + batch if shape == "append" else i
                key = f"{prefix}{index:05d}"
                value = f'{row["time"]},{row["value"]}'
                if shape == "update-delete" and batch and i % 5 == 0:
                    out.append(f"-{key}\t")
                else:
                    out.append(f"+{key}\t{value}")
            out.append("flush")
    return "\n".join(out) + "\n"


def binding():
    return {p.relative_to(ROOT).as_posix(): digest(p.read_bytes())
            for p in (SOURCE, Path(__file__).resolve())}


def summarize(case):
    files = case["files"]
    before, after = set(files["before"]), set(files["after"])
    pool = {name:size for view in files.values() for name,size in view.items()}
    keep = {"before": before, "after": after, "union": before | after, "all": set(pool)}
    if set(files) != {"expired", "before", "after"} or not (before & after and
            before - after and after - before and set(pool) - (before | after)):
        raise ValueError("expected changed, shared, and expired-only tables")
    if any(pool[n] != sz or sz <= 0 for view in files.values() for n,sz in view.items()):
        raise ValueError("inconsistent native SST sizes")
    if [(r["view"],r["policy"]) for r in case["restores"]] != [
            (view, policy) for view in ("before", "after") for policy in keep]:
        raise ValueError("incomplete paired retention comparison")
    for r in case["restores"]:
        required = keep[r["view"]]
        retained = keep[r["policy"]]
        missing = sorted(required - retained)
        if (r["missing"] != missing or r["status"] not in {"exact","open-failed","read-failed"}
                or (r["status"] == "exact") != (not missing)
                or r["early_bytes"] != sum(pool[n] for n in set(pool)-retained)):
            raise ValueError("restore outcome or byte accounting disagrees with inventory")
    return {
        "exact_restores": sum(r["status"] == "exact" for r in case["restores"]),
        "failed_restores": sum(r["status"] != "exact" for r in case["restores"]),
        "union_early_bytes": sum(pool[n] for n in set(pool)-keep["union"]),
        "pool_bytes": sum(pool.values()),
    }


def capture(input_dir):
    with tempfile.TemporaryDirectory(prefix="rf-native-diversity-") as tmp:
        tmp = Path(tmp)
        runner = tmp / "runner"
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++20", "-O2", "-Wall",
                        str(SOURCE), "-lrocksdb", "-o", str(runner)], check=True, timeout=120)
        cases = []
        for name,shape in DESIGN:
            url = f"https://vincentarelbundock.github.io/Rdatasets/csv/datasets/{name}.csv"
            if input_dir:
                raw = (input_dir / f"{name}.csv").read_bytes()
            else:
                with urlopen(url, timeout=30) as response:
                    raw = response.read(100_000)
            batch = tmp / f"{name}.tsv"
            batch.write_text(operations(name,shape,raw), encoding="ascii")
            run = tmp / name
            run.mkdir()
            case = {"dataset": name, "source_url": url, "input_digest": digest(raw),
                    "workload": shape, "files": {}, "restores": []}
            for line in subprocess.check_output([str(runner), str(run), str(batch)],
                                                text=True, timeout=120).splitlines():
                tag,*f = line.split("\t")
                if tag == "engine":
                    case["engine"] = f[0]
                elif tag == "logical":
                    case["sequence"],case["keys"] = map(int,f)
                elif tag == "file":
                    view, filename, size = f
                    case["files"].setdefault(view,{})[filename] = int(size)
                elif tag == "restore":
                    view, policy, status, early_bytes, *missing = f
                    case["restores"].append(dict(view=view,policy=policy,status=status,
                                                 early_bytes=int(early_bytes),missing=missing))
                else:
                    raise ValueError(f"unexpected native record: {tag}")
            case["summary"] = summarize(case)
            cases.append(case)
        return {"sources": binding(), "cases": cases}


def validate(receipt):
    if (receipt["sources"] != binding() or
            [(c["dataset"],c["workload"]) for c in receipt["cases"]] != list(DESIGN)):
        raise ValueError("validation sources or design changed")
    for case in receipt["cases"]:
        name = case["dataset"]
        fp,n = DATA[name]
        keys = n if case["workload"] == "append" else 2 * (n//4 - len(range(0,n//4,5)))
        if (case["input_digest"] != fp or case["sequence"] != n or case["keys"] != keys
                or case["summary"] != summarize(case)):
            raise ValueError(f"native receipt disagrees with design: {name}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--record", type=Path)
    group.add_argument("--run", action="store_true")
    parser.add_argument("--input-dir", type=Path)
    args = parser.parse_args()
    if args.record:
        receipt = validate(capture(args.input_dir))
        with args.record.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
    else:
        receipt = validate(json.loads(RECEIPT.read_bytes()))
        if args.run and validate(capture(args.input_dir)) != receipt:
            raise ValueError("native replay differs from the recorded evidence")
    print(json.dumps({c["dataset"]: c["summary"] for c in receipt["cases"]}, sort_keys=True))


if __name__ == "__main__":
    main()
