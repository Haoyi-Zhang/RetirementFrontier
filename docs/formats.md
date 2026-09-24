# Wire formats and trusted boundaries

## Trace: `retirement-frontier-trace-v1`

A trace is a JSON object with `operations`, issue-ordered `events`, and
`acknowledgements`. Event identifiers are exactly their zero-based positions.
Every dependency names an earlier event.

- A `data` event contains `slot`, positive `generation`, SHA-256 `digest`, byte
  `length`, `data_hex`, `role` (`chunk` or `manifest`), and `deps`.
- A `free` event contains `slot`, descriptive `generation`, and `deps`. It is an
  unconditional tombstone, not a generation-checked conditional deletion.
- A `root` event contains an increasing client `prefix`, a `manifest_ref`, a
  ghost `namespace`, ghost `objects_hex`, and `deps`. The ghost fields are
  admission evidence and are not copied into a crash image.
- An acknowledgement contains an issue boundary `issued`, promised operation
  `prefix`, and earlier durable `anchors`.

A reference has exactly `(slot, generation, digest, length)`. Slot-generation
identities are unique. Admission recomputes digests and lengths, validates the
canonical manifest encoding, checks that complete references reconstruct each
ghost object, and checks that every root equals its client-operation prefix.
A syntactically valid reference with no matching allocation remains an admitted
safety failure; malformed traces are rejected.

## Image: `retirement-frontier-image-v1`

Materialization accepts a dependency-closed event set. It retains only the last
present record for each represented slot and every present immutable root
marker. It does not retain the operation log, ghost namespace, dependency DAG,
or overwritten slot bytes.

Strict recovery selects the greatest present root marker and either reconstructs
it or fails. Fallback recovery scans root markers from greatest to least and
uses the first reconstructible one. A reference read validates generation,
length, digest, and bytes. A successful recovery returns object bytes,
reference counts, reachable slots, unreachable occupied slots, root history,
and recovered prefix.

## Certificate: `retirement-frontier-certificate-v1`

A positive certificate has three complete, deterministically ordered arrays:

- `publication`: one `(root, ref_index, allocation)` row for each distinct
  reference (including the manifest) published by each root;
- `retirement`: one `(writer, guard_root)` row for each destructive writer with
  a nonempty last-user frontier;
- `acknowledgement`: one `(ack_index, root)` row for each acknowledgement, with
  `-1` used only for a zero-prefix promise.

The consumer rejects omitted, reordered, duplicated, or altered rows. The
consumer reparses the trace and intentionally imports none of the producer,
engine, oracle, or solver modules. It shares authorship and the Python runtime;
it is code-separated, not independently developed or mechanically verified.

## Bounds and non-claims

Local JSON inputs are limited to 64 MiB. Identifiers are limited to signed
63-bit nonnegative values in the implementation. The mathematical results use
finite natural identifiers and exact content labels. Whole data and root
records are atomic. Declared dependency-closed sets are the complete crash
model. No device cache, filesystem `fsync`, concurrency, distributed failure,
malicious logger, hash-collision attack, or resumed writes after recovery is
modeled.
