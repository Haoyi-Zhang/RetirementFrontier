"""Project independent production requests and verify native RocksDB restores."""

import argparse
from collections import Counter
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/rocksdb-lifecycle"
INPUT = RESULTS / "external-requests.json.gz"
RECEIPT = RESULTS / "external-checkpoint-replay.json"
SOURCE = Path(__file__).with_suffix(".cc")
REVISION = "1c503676d6972d4394a360e09162149a6c00f516"
CLUSTERS = ("012", "015", "031", "037")  # All upstream-recommended write-heavy clusters.
WINDOW = 8192
VALUE_CAP = 1024
POLICIES = ("before", "after", "union", "all")
VIEWS = ("before", "after")


def fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def source_url(cluster):
    return (f"https://raw.githubusercontent.com/twitter/cache-trace/{REVISION}"
            f"/samples/2020Mar/cluster{cluster}")


def project(lines):
    """Keep source order and key equality without storing source key strings."""
    keys, rows, counts = {}, [], Counter()
    source = hashlib.sha256()
    for i in range(WINDOW):
        row = lines.readline(8192)
        if not row.endswith(b"\n"):
            raise ValueError("incomplete or oversized source row")
        source.update(row)
        time, key, _, size, _, op, ttl = next(csv.reader([row.decode("ascii")]))
        counts[op] += 1
        if op in {"set", "delete"}:
            index = keys.setdefault(key, len(keys))
            size = int(size)
            if size < 0:
                raise ValueError("negative source value length")
            rows.append([i, int(time), index, op, size, int(ttl)])
    return {"source_prefix": source.hexdigest(), "counts": dict(counts), "requests": rows}


def request_projection():
    traces = {}
    for cluster in CLUSTERS:
        request = Request(source_url(cluster), headers={"Range": "bytes=0-4194303"})
        with urlopen(request, timeout=30) as response:
            traces[cluster] = project(response)
    return traces


def operations(trace):
    """Use a second execution model (a Python map) as the native read oracle."""
    rows = trace["requests"]
    counts = Counter(r[3] for r in rows)
    if (sum(trace["counts"].values()) != WINDOW
            or any(trace["counts"].get(op, 0) != counts[op] for op in ("set", "delete"))
            or len(rows) < 4 or set(counts) - {"set", "delete"}):
        raise ValueError("inconsistent source request census")
    keys, output, expected = set(), [], {}
    last = -1
    for n, (index, timestamp, key, op, size, ttl) in enumerate(rows, 1):
        if not last < index < WINDOW or timestamp < 0 or key < 0 or size < 0 or ttl < 0:
            raise ValueError("bad projected request")
        if key not in keys and key != len(keys):
            raise ValueError("unexpected first-occurrence key order")
        keys.add(key)
        last = index
        name = f"k{key:06d}"
        if op == "set":
            # Projected values are deterministic placeholders, never production payloads.
            seed = f"{index:06x}{key:06x}"
            size = min(size, VALUE_CAP)
            value = (seed * ((size + len(seed) - 1) // len(seed)))[:size]
            output.append(f"+{name}\t{value}")
            expected[name] = value
        else:
            output.append(f"-{name}\t")
            expected.pop(name, None)
        if n in {len(rows) * q // 4 for q in range(1, 5)}:
            output.append("flush")
    oracle = "".join(f"{k}\t{v}\n" for k, v in sorted(expected.items()))
    return "\n".join(output) + "\n", oracle, {
        "set_requests": counts["set"], "delete_requests": counts["delete"],
        "live_keys": len(expected), "distinct_projected_keys": len(keys),
        "capped_sets": sum(r[3] == "set" and r[4] > VALUE_CAP for r in rows),
        "nonzero_ttl_sets": sum(r[3] == "set" and r[5] > 0 for r in rows),
    }


def summarize(case):
    files, restores = case["files"], case["restores"]
    if set(files) != set(VIEWS):
        raise ValueError("unexpected checkpoint inventory")
    pool = {name: size for view in VIEWS for name, size in files[view].items()}
    if any(size <= 0 or pool[name] != size
           for view in VIEWS for name, size in files[view].items()):
        raise ValueError("inconsistent SST size")
    keep = {view: set(files[view]) for view in VIEWS}
    keep.update(union=keep["before"] | keep["after"], all=set(pool))
    if [(r["view"], r["policy"]) for r in restores] != [
            (v, p) for v in VIEWS for p in POLICIES]:
        raise ValueError("missing or duplicate paired trial")
    for row in restores:
        retained = keep[row["policy"]]
        missing = sorted(keep[row["view"]] - retained)
        statuses = {"open-failed", "read-failed"} if missing else {"exact"}
        if (row["missing"] != missing or row["status"] not in statuses
                or row["early_bytes"] != sum(pool[n] for n in set(pool) - retained)):
            raise ValueError("native outcome does not match file inventory")
    return {"sst_before": len(keep["before"]), "sst_after": len(keep["after"]),
            "sst_shared": len(keep["before"] & keep["after"]),
            "single_exact": sum(r["status"] == "exact" and r["policy"] in VIEWS for r in restores),
            "union_exact": sum(r["status"] == "exact" and r["policy"] == "union" for r in restores),
            "hold_exact": sum(r["status"] == "exact" and r["policy"] == "all" for r in restores)}


def binding():
    return {p.relative_to(ROOT).as_posix(): fingerprint(p.read_bytes())
            for p in (Path(__file__).resolve(), SOURCE, INPUT)}


def capture(traces):
    with tempfile.TemporaryDirectory(prefix="rf-production-trace-") as directory:
        tmp = Path(directory)
        runner = tmp / "native"
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++20", "-O2", "-Wall",
                        str(SOURCE), "-lrocksdb", "-o", str(runner)], check=True, timeout=120)
        cases = {}
        for cluster, trace in traces.items():
            rows, oracle, census = operations(trace)
            (tmp / "trace").write_text(rows, encoding="ascii")
            (tmp / "oracle").write_text(oracle, encoding="ascii")
            run = tmp / cluster
            run.mkdir()
            case = {"source_url": source_url(cluster), "source_prefix": trace["source_prefix"],
                    "census": census, "files": {v: {} for v in VIEWS}, "restores": []}
            for line in subprocess.check_output([str(runner), str(run), str(tmp / "trace"),
                                                str(tmp / "oracle")], text=True, timeout=60).splitlines():
                tag, *data = line.split("\t")
                if tag == "engine":
                    case["engine"] = data[0]
                elif tag == "logical":
                    case["sequence"], case["keys"] = map(int, data)
                elif tag == "file":
                    view, name, size = data
                    case["files"][view][name] = int(size)
                elif tag == "restore":
                    view, policy, status, released, *missing = data
                    case["restores"].append(dict(view=view, policy=policy, status=status,
                                                 early_bytes=int(released), missing=missing))
                else:
                    raise ValueError(f"unexpected native record: {tag}")
            case["summary"] = summarize(case)
            cases[cluster] = case
    return {"sources": binding(), "cases": cases}


def validate(receipt, traces):
    if tuple(traces) != CLUSTERS or set(receipt["cases"]) != set(CLUSTERS) or receipt["sources"] != binding():
        raise ValueError("unexpected trace design or changed evidence inputs")
    for cluster, case in receipt["cases"].items():
        trace = traces[cluster]
        _, _, census = operations(trace)
        if (case["source_url"] != source_url(cluster) or case["source_prefix"] != trace["source_prefix"]
                or case["census"] != census or case["sequence"] != len(trace["requests"])
                or case["keys"] != census["live_keys"] or case["summary"] != summarize(case)):
            raise ValueError(f"invalid native receipt for cluster {cluster}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--download", type=Path)
    action.add_argument("--verify-source", action="store_true")
    action.add_argument("--record", type=Path)
    action.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.download:
        with args.download.open("xb") as handle:
            handle.write(gzip.compress(json.dumps(request_projection(), separators=(",", ":"),
                                                 sort_keys=True).encode("ascii"), mtime=0))
        return
    traces = json.loads(gzip.decompress(INPUT.read_bytes()))
    if args.record:
        receipt = validate(capture(traces), traces)
        with args.record.open("x", encoding="ascii", newline="\n") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
    else:
        receipt = validate(json.loads(RECEIPT.read_bytes()), traces)
        if args.verify_source and request_projection() != traces:
            raise ValueError("independent source bytes or projection differ")
        if args.run and validate(capture(traces), traces) != receipt:
            raise ValueError("fresh native replay differs from recorded evidence")
    print(json.dumps({c: case["summary"] for c, case in receipt["cases"].items()}, sort_keys=True))


if __name__ == "__main__":
    main()
