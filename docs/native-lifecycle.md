# Physical publication and client-visible state

## Evidence classes

1. **Upstream historical record.** The RocksDB maintainers document that an
   unsuccessful return from MANIFEST write or sync did not identify which
   version edit would replay. Deleting new SSTs while that edit could still
   be selected threatened native reopen. The change defers file deletion
   while the state is unresolved. This is evidence of the historical
   maintenance issue, not a local reproduction or a claim about a current
   release. See [upstream change](https://github.com/facebook/rocksdb/pull/6949)
   and [MANIFEST recovery](https://github.com/facebook/rocksdb/wiki/MANIFEST).
2. **Native observations.** The observer uses only ordinary operations on
   fresh temporary databases. Four synchronized atomic batches over partially
   overlapping key ranges create the pre-compaction table set. The complete
   expected map is computed from
   the bytes submitted by the driver, independently of the table metadata
   and the offline calculation. Read-only opens on each side compare
   every key and value and recover the live table set; a directory inventory
   observes presence and sizes. The active MANIFEST grows in the same writer
   session during compaction. The unchanged client sequence and changed live
   tables distinguish two physical epochs with one logical state.
   Three workloads compact all old tables; a fourth has a disjoint key
   range and leaves its table live on both sides of range compaction.
   The observer uses a fixed experimental host label in SST properties so
   another machine can reproduce the byte counts without sharing its hostname.
3. **Offline comparison.** For a supplied finite set of views with exact
   required-file sets, and no alternate copy within a view, the union is
   the unique inclusion-minimal file set that covers all supplied
   references: omitting any union member leaves its corresponding view
   without a required file, and retaining the union covers every reference.
   This is a statement about file availability, not a guarantee of
   end-to-end recovery. The two one-view controls use the same observed file
   catalog. Each misses files from the other view; their union saves no
   files over retaining that entire catalog. The runs do not prove the two
   snapshots are exhaustive or even simultaneous crash candidates.

## From native edits to a checkable obligation

The same writer captures the complete manifest at two successful I/O
boundaries. The second capture must extend the first byte for byte. Stock
RocksDB tooling independently decodes both, and each final table membership,
table size and sequence is checked against the separate library observation.
The suffix must contain exactly one table-membership edit. That edit must
account for every added or removed table. The artifact retains normalized
decoded fields rather than timestamps and random native identifiers.

The bounded projection represents each table as a distinct immutable
presence token. Each decoded publication references exactly its required
tokens. Table removal maps to a free. The abstract namespace contains one
opaque object for this obligation, not the native keys or payload. Both
publications share that single fixed abstract prefix, so the projection
introduces no client update for the compaction.

The ordered profile requires publication to depend on the new table and
removal to depend on the new publication. Other profiles weaken one or both
edges in the abstract graph only. For each legal cut, an independent oracle
selects the corresponding native table set and checks projected file
availability directly. The main checker compares every cut against it, and
the separate certificate consumer verifies the ordered case.

The separate unresolved-publication check assumes both candidate file
sets are already available before cleanup. It enumerates removal subsets
for three states: only the old view confirmed, both views still possible,
and only the new view confirmed. It compares the guard with a direct
file check for each admissible view, including the shared SST in the
range case. The one-view controls are evaluated only in this abstract
comparison; no native errors are injected.

This bridge covers a bounded, whole-edit, one-writer replacement profile.
It is not an assertion that all tested cuts occur on a particular filesystem,
a test of torn metadata or manifest rotation, or a reconstruction of native
table contents. Native reopen and full client-byte equality are separately
checked on the real engine.

The SQLite adapter is a different kind of evidence. Its own object-store
records cross multiple successful SQLite transactions; it tests the full
model bridge in that controlled setting and does not claim that SQLite's
atomic transactions fail.

## Paired native checkpoints

A separate fixture makes two disjoint range compactions with no intervening
client writes and retains a native checkpoint at all three boundaries. It
uses two of the same public SQLite headers as values. The first checkpoint
is an expired backup; the other two are declared recovery obligations. The
observer independently reopens all three, verifies every key and value,
and tests that shared SSTs are hard links to the same physical file.

The observer disposes of the expired checkpoint in its own temporary
directory, only after checking that its exclusive SSTs have one link and
are not in the live database. It then reopens both surviving checkpoints
and verifies their data and live SST sets again. The removed SST lengths
are logical file lengths, not measurements of free filesystem blocks.
Offline controls consider every removal subset of the observed SST catalog
against both surviving views. The result establishes a benefit of
selective retention over holding the entire observed catalog; it does
not identify candidate states of a failed native MANIFEST publication.

## Publication handoff with real transactions

The owned fixture separates the SQLite metadata transaction from the
lifetime of files outside that transaction. It synchronizes payloads and
a pending record containing both complete layouts before publication.
A single publisher inserts the new layout and advances the selector in
one SQLite transaction. The controlled boundaries are before commit
and after commit but before the completion reply. The cleaner cannot
distinguish them from its restricted handoff inputs: the same worker
exit, no output, the same pending record, and the same file inventory.

Two shapes (segment merge and split), two journal modes and the two
boundaries are crossed with two executable policies. The candidate
guard unlinks only files outside both layouts before reading SQLite;
the resolve-first baseline waits to read the selected layout. After
resolution both policies remove the files outside the selected layout.
Native recovery checks SQLite integrity, exact metadata and every
logical object byte before and after final cleanup. Choosing either
layout without resolving it is evaluated against actual recovered
references only; no unsafe deletions are executed. File counts and
logical lengths measure work moved before resolution, not speed,
filesystem block usage, or extra eventual reclamation.

The controlled process exits are in the application, not SQLite's
commit implementation. The pending record and exclusive file ownership
are part of the protocol; a corrupted or incomplete candidate record
does not justify cleanup. This fixture does not reproduce the historical
RocksDB defect or claim which recovery view a faulty RocksDB instance
would choose.

## Native deduplicating repository

The [restic design](https://restic.readthedocs.io/en/v0.16.4/design.html)
describes replacement-pack publication, index update, and old-pack deletion
as separate maintenance steps. The local observation uses the installed
restic 0.16.4 executable. The first snapshot contains two public SQLite
headers; the second retains one and substitutes the third header.

The native snapshot tree supplies blob identities. The native index
supplies the packs containing each blob. The test checks the complete
restored file set against the input files byte for byte, both before and
after ordinary cleanup, and invokes restic's full-data check after pruning.
It sees one live data blob relocate even though the surviving snapshot's
identity and logical contents remain unchanged. Pack identifiers can differ
between reruns because a new repository uses randomized encryption.
The retained receipt preserves the first accepted observation, and a rerun
compares the outcome counts rather than encryption-dependent identifiers.

This observation verifies the distinction between a snapshot and a
physical location; it neither reports a failure in restic nor treats its
potential alternate blob locations as unique model incarnations.
