# Model, implementation and experiment boundaries

## What is proved

The written arguments in `theorems.md` assume a finite serial trace, atomic records (including a whole flat manifest), unique physical incarnations, immutable roots, and an exact backward persistence dependency graph. Client operations define legal namespaces independently of certificate conditions. Recovery is independently defined over bytes. Publication, retirement and acknowledgement conditions are then proved equivalent to all-cut strict-recovery safety. The finite census does not establish the general theorem.

Last-user aggregation is a maximum over the totally ordered serial roots. It is not an arbitrary-poset antichain compression theorem. Pairwise interference is aggregated into one threshold per destructive writer. After last-user collection, current producer and consumer scan each slot's writers once with a running maximum; FREE does not reset that maximum. Producer closure storage, publication searches, byte validation and minimum-witness pair enumeration retain their separate costs. Minimum negative witnesses use all relevant root/writer pairs, not just the maximum root.

The fallback reduction has arbitrary unbounded candidate roots and explicitly encoded content/identifiers. It deliberately fails publication P. Publication-valid traces admit a separate polynomial deletion decision with a greatest violating witness, even with unbounded candidates. Fixed candidate count yields exact enumeration with minimum-cardinality witnesses. The concrete implementation admits bounded-width identifiers and SHA-256 references. A finite digest space is not used to justify an unbounded hardness theorem.

## What programs check

The producer and separate certificate consumer enforce strict admission, including role binding, canonical manifest encoding, exact namespace reconstruction and numeric types. The consumer does not import the producer, oracle, engine or SMT implementation; both are nevertheless developed in the same project. The byte oracle and SMT encoding share some definitions or admission routines and are not organization-independent replications.

The release validator checks precise primary rows and summaries. A passing validator demonstrates consistency of retained evidence, not absence of all implementation defects. Untrusted traces may consume substantial memory within configured file/choice bounds; no denial-of-service resilience claim is made.

## What the POSIX illustration does not establish

The file helper completes write, flush, file fsync, rename and directory fsync before any injected termination. Its root is selected by a CURRENT file, not by scanning prepared root files. The completed-helper projection in `selector-correspondence.md` treats prepared files as manifests and CURRENT changes as logical ROOT publications, proving selector/byte agreement on those fixed prefixes. No crash inside an individual helper, physical power loss, firmware behavior, block tearing or certificate consumption is tested. Eleven retained observations include two unchanged initial-state controls, two normal completions, and seven actual injected process terminations; the two stale-generation failures occur in the unsafe ordering. Windows validation checks abstract projection outcomes without running or simulating those syscalls. This is not a general filesystem refinement or a production storage implementation.

## Empirical scope

### SQLite transaction-to-event correspondence

The additional SQLite adapter commits exactly one logical DATA or ROOT record
per transaction, together with its audit entry. A DATA transaction replaces one
row keyed by logical slot and a ROOT transaction inserts an immutable publication
plus the client operation. All transactions use synchronous=FULL in DELETE or
WAL mode. In one-writer order, event i depends on i-1. Assuming SQLite transaction
atomicity and retention of returned commits, a reopen sees a prefix of whole
events, not partial DATA/ROOT records. Slot rows equal the last DATA per key;
root rows equal the committed ROOT events. Native recovery selects the greatest
publication and validates its referenced manifest and chunk incarnations.
Induction over committed transactions establishes native/model prefix and byte
correspondence at this interface. The audit is an instrumented input binding,
not an authenticated log or independent observer. A corruption regression
changes slot bytes while retaining the log and demonstrates that native
recovery rejects instead of trusting the still-safe transcript.

Protecting every chunk and the manifest reachable from the previous publication
until the next ROOT commit preserves publication and retirement. Protecting only
the working namespace would be wrong when replacing an object: the previous
root remains selectable during DATA commits. Unpublished orphan records after
an interrupted write may be reused after reopen because they have no root user.
The allocation rule does not run the offline checker on the hot path; generated
traces are subsequently certified and checked by the code-separated consumer.

The retained campaign exits a child with os._exit at every before-BEGIN,
after-state, after-audit and after-COMMIT event boundary for the declared short
workloads. State/audit changes before COMMIT must roll back together. Each
safe recovered prefix is compared to native reads, model materialization and a
client-intent byte oracle, then extended by a new write. The unsafe control
deliberately overwrites a still-selected incarnation. These are own-protocol
negative controls, not newly discovered SQLite bugs. The experiment does not
interrupt SQLite inside its COMMIT implementation, cut power or clear host
caches. Recovery trusts SQLite's atomicity rather than verifying the engine.

Forty-eight paired-policy runs use 32 operations in shared, unique, churn and
backup families, three seeds/repetitions and two journal modes. The backup
family stores actual SQLite backup images from a changing twelve-account
database; the recovered last image is opened by SQLite and checked for
integrity and an independently computed count/sum query. Backup repetitions
reuse that same fixed source workload. Both policies use identical intent bytes,
chunking, transaction counts, FULL synchronization, audit and validation paths,
with alternating order. Reported file sizes include audit history and open
sidecars after an explicit checkpoint; they are not physical I/O or live-payload
sizes. Recorded instrumented lifecycle times include initialization and client
validation, and do not establish production throughput or a population speedup.

The adapter does not broaden the theorem to concurrent clients, device slots,
bounded root rings, torn records or production garbage collectors. See SQLite's
actual contracts at https://www.sqlite.org/atomiccommit.html,
https://www.sqlite.org/wal.html and
https://www.sqlite.org/pragma.html#pragma_synchronous.

The strict latest-root helper extension is separate from the eleven retained
selector-process observations. It compares 56 initialization and 130 update
images under two supplied rename-retention rules, using real file writes,
flushes and replacements but mocked synchronization and directory calls.
All 186 native/model outcomes agree; the ordered updates have zero failures and
unsafe reuse has 18 stale-generation failures. These checks do not infer
filesystem or physical power-loss behavior from the supplied retention rules.

Regression inputs are not untouched. Zero disagreement is limited to executed overlapping domains, not all algorithms on every scale. Seven repeat recoveries per image establish deterministic read-only results in this model, not recovery that tolerates repeated crashes during mutation. Unreachable occupied slots can be erased only in the stated quiescent selected-root setting, not while old snapshots or concurrent readers remain pinned.

The public-byte set is narrow and intentionally unchanged across versions. Reported cost ratios are model encoding costs. No device measurements, representative workload study, independent human evaluation, or acceptance estimate is claimed.

## Generator and offline image boundary

`TraceBuilder` updates in-memory specification maps and appends events; it has no
separate online medium or medium-backed read API. The harness admits the emitted
trace and independently materializes chosen cuts, then recovery reads the image.
The small-domain oracle compares recovered bytes with ghost expectations.
The 720-image campaign compares selected-root identity, prefix, and object
bytes with the specification after the timed recovery calls. It also checks
repeatability, multiplicity, and quiescent reclamation, and its root/event rank
strata record exact cut IDs. Separate local image-file checks in
`results/recovery-subclasses/` complement these observations. Both examine
serialization and byte recovery in the atomic-record model.
Whole-trace binding admission checks later allocations as well as earlier ones.
Neither offline reconstruction nor the separate POSIX illustration establishes
an online implementation that is absent from this artifact.

Costs use the modeled compact JSON descriptor plus one separator byte and, for
data, raw payload bytes. They exclude trace `id/deps/data_hex` and ghost fields.
See `formats.md` for the exact per-kind field sets.
