"""Verify paired Apache Spark RocksDB compatibility checkpoints offline."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "results/rocksdb-lifecycle/spark-checkpoints.json"
FIXTURE = ROOT / "inputs/spark-checkpoints.zip"
READER = Path(__file__).with_name("spark_checkpoint_reader.cc")
REVISION = "29434ea766b0fc3c3bf6eaadb43a8f931133649e"
UPSTREAM = f"https://raw.githubusercontent.com/apache/spark/{REVISION}"
PATH = "sql/core/src/test/resources/structured-streaming/checkpoint-version-4.0.0/rocksdb"
WORKLOADS = ("agg", "dedup")
PARTITIONS = tuple(range(5))
VIEWS = (2, 4)
POLICIES = ("early", "late", "union")
LOCAL = re.compile(r"[0-9]{6}\.sst")
REMOTE = re.compile(r"[0-9]{6}-[0-9a-f-]{36}\.sst")
META = re.compile(r"CURRENT|MANIFEST-[0-9]{6}|OPTIONS-[0-9]{6}|[0-9]{6}\.log|IDENTITY")


def fetch(name):
    location = name if name in {"LICENSE", "NOTICE"} else f"{PATH}/{name}"
    with urlopen(f"{UPSTREAM}/{location}", timeout=20) as response:
        data = response.read(100_001)
    if not 0 < len(data) <= 100_000:
        raise ValueError(f"unexpected download size for {name}")
    return data


def snapshot(data):
    with ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        if (len(names) != len(set(names)) or "metadata" not in names or "CURRENT" not in names
                or any(n != "metadata" and not META.fullmatch(n) for n in names)
                or any(i.file_size > 100_000 for i in z.infolist())):
            raise ValueError("unexpected snapshot archive members")
        meta = {n: z.read(n) for n in names if n != "metadata"}
        if meta["CURRENT"].decode("ascii").strip() not in meta:
            raise ValueError("missing selected manifest")
        version, body = z.read("metadata").split(b"\n", 1)
        if version != b"v1":
            raise ValueError("unknown Spark snapshot metadata encoding")
        mapping = json.loads(body)
    seen, local = {}, set()
    for row in mapping.get("sstFiles", []):
        name, remote, size = (row[k] for k in ("localFileName", "dfsSstFileName", "sizeBytes"))
        if (not LOCAL.fullmatch(name) or not REMOTE.fullmatch(remote) or name in local
                or remote in seen or type(size) is not int or not 0 < size <= 100_000):
            raise ValueError("invalid or duplicate Spark SST mapping")
        local.add(name)
        seen[remote] = (name, size)
    keys = mapping["numKeys"]
    if type(keys) is not int or keys < 0:
        raise ValueError("invalid Spark key count")
    return meta, seen, keys


def plan(data):
    members = {"LICENSE", "NOTICE"}
    for workload in WORKLOADS:
        for view in VIEWS:
            name = f"{workload}/commits/{view-1}"
            if not data[name].startswith(b"v1\n"):
                raise ValueError("missing Spark batch completion record")
            json.loads(data[name].split(b"\n", 1)[1])
            members.add(name)
    for workload in WORKLOADS:
        for partition in PARTITIONS:
            stem = f"{workload}/state/0/{partition}"
            views = {}
            for view in VIEWS:
                name = f"{stem}/{view}.zip"
                members.add(name)
                meta, tables, keys = snapshot(data[name])
                members.update(f"{stem}/SSTs/{n}" for n in tables)
                views[view] = (meta, tables, keys)
            yield workload, partition, stem, views
    if set(data) != members:
        raise ValueError("fixture contains missing or unused source files")


def acquire():
    names = ["LICENSE", "NOTICE"] + [
        f"{w}/commits/{v-1}" for w in WORKLOADS for v in VIEWS
    ] + [f"{w}/state/0/{p}/{v}.zip" for w in WORKLOADS for p in PARTITIONS for v in VIEWS]
    with ThreadPoolExecutor(max_workers=8) as pool:
        data = dict(zip(names, pool.map(fetch, names), strict=True))
        remotes = sorted({f"{name.rsplit('/', 1)[0]}/SSTs/{n}" for name, raw in data.items()
                          if name.endswith(".zip") for n in snapshot(raw)[1]})
        data.update(zip(remotes, pool.map(fetch, remotes), strict=True))
    list(plan(data))
    return data


def load():
    with ZipFile(FIXTURE) as z:
        names = z.namelist()
        if len(names) > 80 or len(names) != len(set(names)) or any(i.file_size > 100_000 for i in z.infolist()):
            raise ValueError("invalid fixture size or inventory")
        return {n: z.read(n) for n in names}


def invoke(reader, path):
    run = subprocess.run([reader, path], check=True, capture_output=True, text=True, timeout=15)
    lines = run.stdout.splitlines()
    if lines in (["open-failed"], ["read-failed"]):
        return {"status": lines[0]}
    if not lines or lines[0] != "ok":
        raise ValueError("invalid RocksDB reader response")
    out = {"status": "ok", "files": {}, "rows": []}
    for line in lines[1:]:
        tag, *values = line.split("\t")
        if tag == "file" and len(values) == 2:
            name, size = values
            if name in out["files"]:
                raise ValueError("duplicate live SST")
            out["files"][name] = int(size)
        elif tag == "row" and len(values) == 2:
            out["rows"].append(values)
        elif tag in {"engine", "sequence"} and len(values) == 1 and tag not in out:
            out[tag] = values[0]
        else:
            raise ValueError("unexpected reader field")
    return out


def evaluate(data):
    cases = []
    with tempfile.TemporaryDirectory(prefix="rf-spark-checkpoints-") as name:
        root = Path(name)
        reader = root / "native"
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-O2", "-Wall",
                        READER, "-lrocksdb", "-o", reader], check=True, timeout=120)
        for workload, partition, stem, views in plan(data):
            scope = root / f"{workload}-{partition}"
            scope.mkdir()
            retained = [set(views[v][1]) for v in VIEWS]
            keep = dict(zip(POLICIES, (*retained, set.union(*retained)), strict=True))

            def restore(view, policy, subset):
                meta, tables, _ = views[view]
                dst = scope / f"{view}-{policy}"
                dst.mkdir()
                for n, raw in meta.items():
                    (dst / n).write_bytes(raw)
                for remote in subset & tables.keys():
                    local, size = tables[remote]
                    raw = data[f"{stem}/SSTs/{remote}"]
                    if len(raw) != size:
                        raise ValueError("Spark SST size does not match")
                    (dst / local).write_bytes(raw)
                return invoke(reader, dst)

            case = dict(workload=workload, partition=partition, states={}, comparisons=[])
            for view in VIEWS:
                _, tables, keys = views[view]
                native = restore(view, "intact", set(tables))
                if (native["status"] != "ok" or len(native["rows"]) != keys
                        or native["files"] != dict(tables.values())):
                    raise ValueError(f"Spark count or manifest mismatch for {stem}/{view}: {native}")
                case["states"][str(view)] = native
                for policy in POLICIES:
                    actual = restore(view, policy, keep[policy])
                    absent = sorted(tables.keys() - keep[policy])
                    if (not absent and actual != native) or (absent and actual["status"] == "ok"):
                        raise ValueError("unexpected retention comparison")
                    case["comparisons"].append(dict(view=view, policy=policy, missing=absent,
                                                     status=actual["status"]))
            cases.append(case)
    return {"source_revision": REVISION,
            "input_files": {n: hashlib.sha256(b).hexdigest() for n, b in sorted(data.items())},
            "cases": cases}


def summarize(result, data):
    """Validate provenance, coverage, and the receipt before rendering counts."""
    if result["source_revision"] != REVISION or result["input_files"] != {
            n: hashlib.sha256(b).hexdigest() for n, b in data.items()}:
        raise ValueError("unbound Apache Spark fixture")
    summary = {w: {"partitions": 0, "changed": 0, "trials_per_policy": 0,
                   **{p: 0 for p in POLICIES}} for w in WORKLOADS}
    for (workload, partition, _, views), case in zip(plan(data), result["cases"], strict=True):
        if (case["workload"], case["partition"]) != (workload, partition):
            raise ValueError("Spark partition does not match the fixed corpus")
        tally = summary[workload]
        sets = [set(views[v][1]) for v in VIEWS]
        keep = dict(zip(POLICIES, (*sets, set.union(*sets)), strict=True))
        tally["partitions"] += 1
        tally["changed"] += sets[0] != sets[1]
        tally["trials_per_policy"] += len(VIEWS)
        for view, (_, tables, keys) in views.items():
            state = case["states"][str(view)]
            if (state["status"] != "ok" or state["files"] != dict(tables.values())
                    or len(state["rows"]) != keys):
                raise ValueError("bad intact checkpoint receipt")
        for (v, p), row in zip(((v, p) for v in VIEWS for p in POLICIES),
                               case["comparisons"], strict=True):
            absent = sorted(views[v][1].keys() - keep[p])
            if (row["view"], row["policy"], row["missing"]) != (v, p, absent):
                raise ValueError("wrong inventory or policy")
            if row["status"] not in ({"open-failed", "read-failed"} if absent else {"ok"}):
                raise ValueError("restore disagrees with retained file coverage")
            tally[p] += row["status"] == "ok"
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--download", type=Path)
    action.add_argument("--record", type=Path)
    action.add_argument("--verify", action="store_true")
    action.add_argument("--verify-source", action="store_true")
    action.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    if args.download:
        data = acquire()
        with ZipFile(args.download, "x") as z:
            for name, content in sorted(data.items()):
                z.writestr(name, content)
        return
    data = load()
    if args.summary:
        print(json.dumps(summarize(json.loads(RECEIPT.read_bytes()), data), sort_keys=True))
        return
    if args.verify_source:
        if data != acquire():
            raise ValueError("bundled bytes differ from pinned Apache Spark source")
        print(f"Verified {len(data)} original upstream files")
        return
    result = evaluate(data)
    summarize(result, data)
    if args.record:
        with args.record.open("x", encoding="ascii", newline="\n") as f:
            json.dump(result, f, indent=2, sort_keys=True)
            f.write("\n")
    elif result != json.loads(RECEIPT.read_bytes()):
        raise ValueError("fresh native checkpoint evaluation differs from retained receipt")
    for workload in WORKLOADS:
        group = [c for c in result["cases"] if c["workload"] == workload]
        outcomes = [r for c in group for r in c["comparisons"]]
        print(workload, " ".join(f"{p}={sum(r['policy'] == p and r['status'] == 'ok' for r in outcomes)}"
                                 f"/{len(group)*len(VIEWS)}" for p in POLICIES))


if __name__ == "__main__":
    main()
