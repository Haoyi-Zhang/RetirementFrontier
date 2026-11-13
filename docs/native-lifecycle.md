# Physical publication and client-visible state

## Evidence classes

1. **Reported production incident.** In [RocksDB issue #9419](https://github.com/meta/rocksdb/issues/9419),
   the shutdown log records deletion of table 48510, and the subsequent
   reopen error names the same table number under its legacy extension.
   The reporter says that restoring the deleted tables from a snapshot
   allowed reopen. The public report does not provide the active MANIFEST
   and its selection record at the failed reopen, so it does not identify
   the edit history or deletion path responsible for the missing table.
   The deletion text and structured deletion events agree on four table
   numbers; 48510 is the only one named in the displayed reopen error.
   In a separate [operator report (#6430)](https://github.com/meta/rocksdb/issues/6430),
   CURRENT and its selected MANIFEST stopped updating while compaction
   continued and removed referenced tables. Restart then reported missing
   files. This describes a distinct observation, not the cause of #9419.
   Neither report is an experiment in this artifact.
2. **Documented engine mechanism.** The RocksDB maintainers document that an
   unsuccessful return from MANIFEST write or sync did not identify which
   version edit would replay. Deleting new SSTs while that edit could still
   be selected threatened native reopen. The change defers file deletion
   while the state is unresolved. The
   [merged change](https://github.com/meta/rocksdb/commit/e66199d848cd484b816d07359f1dc0f0b99e5351)
   is [already in the upstream release named by the reporter](https://github.com/meta/rocksdb/compare/e66199d848cd484b816d07359f1dc0f0b99e5351...v6.15.5).
   It establishes a maintenance failure mode, not the root cause of #9419
   or a defect in a current release.
   Independently, [FLINK-25872](https://issues.apache.org/jira/browse/FLINK-25872)
   records an ownership-transition bug: a subsumed checkpoint's
   materialized state could still be needed by a newer checkpoint.
   [The maintainer fix](https://github.com/apache/flink/commit/798d83a0fb4b5faa16cb57c93eebb88093613fbc)
   makes cleanup account for inherited handles. The
   [Flink 2.0 paper](https://www.vldb.org/pvldb/vol18/p4846-mei.pdf),
   Section 5.2, describes the analogous lifetime guarantee at a different
   boundary: ForSt delegates physical reclamation to its filesystem, which
   counts links from checkpoint and working state. These are external
   documentary sources, not extra replay cases or evidence that the
   retirement-frontier implementation is integrated into Flink.
3. **Native observations.** The observer uses only ordinary operations on
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
4. **Offline comparison.** For a supplied finite set of views with exact
   required-file sets, and no alternate copy within a view, the union is
   the unique inclusion-minimal file set that covers all supplied
   references: omitting any union member leaves its corresponding view
   without a required file, and retaining the union covers every reference.
   This is a statement about file availability, not a guarantee of
   end-to-end recovery. The two one-view controls use the same observed file
   catalog. Each misses files from the other view; their union saves no
   files over retaining that entire catalog. The runs do not prove the two
   snapshots are exhaustive or even simultaneous crash candidates.

## Independent Spark checkpoint files

The artifact includes a small selection from the
[Apache Spark compatibility corpus](https://github.com/apache/spark/tree/29434ea766b0fc3c3bf6eaadb43a8f931133649e/sql/core/src/test/resources/structured-streaming/checkpoint-version-4.0.0/rocksdb).
It covers the aggregation and deduplication jobs, all five partitions of
each job, and both stored state snapshots (2 and 4). The corresponding
Spark batch-completion records are 1 and 3. This is a selection of
unchanged source files, repackaged with Spark's original LICENSE and
NOTICE inside the input archive. No new upstream checkpoint was created.

Run `python -B scripts/spark_checkpoint_validation.py --verify` with a
C++ compiler and the pinned RocksDB development library to reproduce
the native results offline. `--verify-source` independently downloads
the same selection and compares every original member. The reader
assembles temporary copies using Spark's remote-to-local SST mapping,
keeps the archived native metadata unchanged and opens RocksDB read-only.
Intact native scans must agree with Spark's recorded key counts and
referenced SST sizes; each successful retained-file trial must match
the corresponding intact scan byte for byte, at the same sequence.
The three policies differ only in which original SSTs they supply.

All 20 intact snapshots opened. Earlier-only retention recovered
15/20 views, later-only 20/20, and the union 20/20. Five later views
needed an additional SST and could not open under earlier-only retention.
Every earlier table in this corpus survived in the later file set.
The two jobs are the workload units; the partitions and retention
trials are not independent workloads. The observation complements
the controlled compaction and production-request replay: these
snapshots are real upstream compatibility artifacts, not production
captures, observed deletion events, or a rerun of the Spark queries.

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

The staged-restore comparison uses the same native checkpoint procedure.
For each choice of recovery metadata, an external backup manager retains
one candidate's SSTs, their union, or the entire six-table pool. An unchanged
copy of the selected checkpoint's metadata goes to the restore directory
with the surviving SSTs. Native read-only reopen rejects both wrong-view
staged backups, while both union-retention attempts recover all expected
values at the original sequence, as do the hold-all controls. The union
removes two expired-only tables before selection; hold-all removes none.
After selection, both retain the same SST bytes for that checkpoint. The
measurement counts file lengths, not allocated filesystem blocks. The
source database and surviving checkpoints are not changed by this
comparison. This is an application backup failure, not a replay of an
internal RocksDB MANIFEST error.

## Publication handoff with real transactions

The transaction changes the catalog's selector and manifest together.
Filesystem cleanup acts on application-owned files outside that transaction.
The companion transaction-boundary control applies each of three policies
in fresh disposable directories before reopening the catalog. It verifies
the atomic outcome and integrity of the SQLite catalog separately from
byte reconstruction. A wrong guessed layout can leave a missing external
segment despite an intact catalog. Protecting both complete layouts avoids
that failure in the same before/after comparison. The experiment is not
a reproduction of the upstream RocksDB report.

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

## Reader lifetimes after confirmed publication

A separate WAL fixture holds read transactions on two earlier layouts
while a writer advances the selector to a third layout of the same
logical objects. A lock serializes snapshot acquisition and pin
registration with publication and deletion; releasing a pin occurs only
after the reader closes its transaction. Readers then use SQLite's
normal WAL snapshots and read their real external segment files.

The cleaner checks the current layout and all registered layouts before
unlinking. The wait-for-all baseline defers unlinks until both readers
release. The guarded strategy instead unlinks an archive file while both
are active and reclaims each layout's exclusive segments as its last
reader releases. Both release orders are tested, including a native byte
check by the remaining reader after the other one releases. An additional
controlled exit leaves a stale pin, which is retained rather than
silently expired. The test does not infer arbitrary reader lifetimes or
combine reader exits with uncertain writer publication.

## Unresolved publication and concurrent reader registration

The composition fixture pins the first layout, confirms a second
layout, and durably records a ticket for the possible second and
third layouts. A publisher stops before commit or after commit but
before reply. Under the same application lock, the cleaner protects
all ticket candidates and all registered layouts, including the first
layout, without opening SQLite or its journal.

We cross both publication outcomes with both registration orders and
the resolve-first baseline. The newcomer either registers before
cleanup or observes contention on the actual lock held by the
cleaner. A subsequent authoritative SQLite read checks the recovered
selector and layout and resolves the ticket. Both readers independently
check their snapshot and object bytes before and after that read.
The two files exclusive to the first layout illustrate why the
publication candidates alone are insufficient. No unsafe unlink
is attempted. This is one serial publisher with cooperating readers
and two controlled schedules, not an arbitrary concurrency model.

The restart experiment adds two unreferenced spare files so a partial
cleanup leaves work for the next process. An independent inventory
oracle checks the initial directory, the files visible after each
interruption, the newly computed deletion set, and the drained state.
The restart never consumes a saved plan. It rereads the pending ticket
and pins under the gate, and a released pin makes its two exclusive
segments eligible before ticket resolution. Both sides of the first
directory synchronization are observed with the kernel still running;
the unsynchronized unlink's visibility is not evidence of persistence
after a reboot. The uninterrupted controls use the same readers,
publisher outcomes, and release schedule.

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
