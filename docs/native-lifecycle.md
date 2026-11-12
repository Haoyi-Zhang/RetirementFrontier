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

The model's strict recovery orders immutable, byte-bearing publications
and assumes increasing client-operation prefixes. RocksDB can publish a
different table set at the same client sequence number; the experiment
does not insert an invented client write to make its evidence fit that
assumption. Deriving admissible crash views from native MANIFEST persistence
and mapping file reachability to model allocations would be separate
requirements for applying the full theorem.

The SQLite adapter is a different kind of evidence. Its own object-store
records cross multiple successful SQLite transactions; it tests the full
model bridge in that controlled setting and does not claim that SQLite's
atomic transactions fail.

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
