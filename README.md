# Retirement Frontiers

Executable trace-level crash certificates for deduplicating stores, accompanying the original research article prepared for **ACM Transactions on Storage (TOS)**.

Paper authors, in order: Haoyi Zhang, Huaijin Ran, Yuqiang Sun,
[Jiongchi Yu](https://orcid.org/0000-0002-2888-4499), and
[Yang Liu](https://orcid.org/0000-0001-7300-9215).
Corresponding author: Yang Liu, Nanyang Technological University,
yangliu@ntu.edu.sg.

## Scope

The finite model has serial events, atomic byte-bearing DATA records (including whole manifests), FREE records, immutable retained ROOTs, unique physical incarnations, and an explicit backward dependency DAG. A crash cut is downward closed. Strict recovery selects the greatest durable ROOT. Acknowledgements promise a recoverable operation prefix, not indefinite retention of overwritten object versions.

Strict safety requires successful reconstruction and every compatible
acknowledgement. Fallback instead selects the greatest valid root and checks
acknowledged-prefix safety: a damaged newest root is permitted if some valid
candidate still reaches each promised prefix. Its publication-valid decision
returns a greatest violating cut; fixed-candidate complete search can return
a minimum-cardinality one. These outputs have different objectives.

The mathematical results characterize this model, not arbitrary programs or hardware. The byte-bearing model, exact oracle, separate certificate consumer, optional SMT encoding, and POSIX illustration have distinct trust boundaries. The POSIX example uses a current-root selector and whole-file replacement helpers. An exact completed-helper projection maps prepared root files to manifests and selector updates to ROOT publications; it is not an all-state filesystem refinement or a power-loss experiment. See `docs/selector-correspondence.md`.

Applying the theorem to another store requires a complete issued-event trace,
justified backward persistence edges, whole-record atomicity, non-wrapping
physical generations, retained immutable publications and the declared recovery
policy. Physical root-ring capacity is not a bound on the number of selectable
immutable root events without a marker-replacement model. The current helpers
illustrate parts of this adapter contract; no production trace extractor is
included.

## SQLite engine adapter

`src/sqlite_store.py` implements an owned, one-writer deduplicating object store
over the real SQLite engine. `put`, `get`, `delete`, reopen and trace export are
functional operations, not offline image serialization. Logical slots are
SQLite rows, not disk addresses. One DATA or ROOT event and its audit row commit
in one FULL-synchronization transaction. Native recovery reads only the slot
and root tables; it does not trust the audit transcript or call model recovery.
The conservative no-reuse policy has the same chunking, commits and audit path.
The unsafe policy is an explicit negative control, not a supported deployment.

```sh
python -B scripts/run_sqlite_adapter.py
python -B scripts/check_sqlite_adapter.py
python -B scripts/run_tests.py --pattern test_sqlite_store.py -v
python -B src/sqlite_store.py /tmp/owned-object-store.db put example /path/to/input
python -B src/sqlite_store.py /tmp/owned-object-store.db list
```

Use a local filesystem for the campaign. It terminates only its own temporary
child processes and never targets an existing user database. The retained Linux
run has 376 abrupt process exits, 304 safe-policy recovery/resume checks and 32
expected native failures among 72 unsafe controls, with no native/model
disagreement. Forty-eight matched cost runs include twelve SQL backup restores
whose integrity and account query are checked by SQLite itself. Backup repeats
use the same controlled evolving source database, not independent field traces.
Complete settings and raw rows are in `scripts/sqlite_experiment.json` and
`results/sqlite-adapter/`; source bindings are checked before publication-table
generation. Physical file totals include retained audits and open sidecars;
they are not device-write counts. Timing includes initialization and client-byte
validation. The supplied commands require only Python's SQLite standard module.

This is a real-engine adapter for the stated application-level contract, not an
adapter for all SQLite internals or an existing production deduplicating store.
The serial commit dependency chain deliberately excludes asynchronous commit
reordering. Process exit does not clear kernel caches, and neither the SQLite
atomic-commit assumption nor hardware power-loss behavior is re-proven here.
See the precise correspondence and limitations in `docs/trust-boundaries.md`.

## Strict latest-root helper checks

The strict `recover_latest` path selects the numerically greatest retained root
file independently of `CURRENT`. Optional helper hooks expose nine successful
I/O boundaries. `tests/test_posix_event_cuts.py` compares 56 initialization and
130 update images against the existing model under two explicitly supplied
rename-retention rules. It executes file writes, flushes and replacements while
mocking synchronization and directory calls. All 186 comparisons agree;
ordered updates have no failures and unsafe reuse has 18 stale-generation
failures. The original eleven completed-selector observations remain unchanged.
The test-local retention rules are assumptions, not measured power-loss behavior.

```sh
python -B -m unittest discover -s tests -p test_posix_event_cuts.py -v
python -B -O -m unittest discover -s tests -p test_posix_event_cuts.py -v
```

## Execution requirements

All four optional paper figures use Python ReportLab and `requirements-figures.txt`.
They embed the installed Type 1 counterpart of the manuscript's Libertine face:

```sh
python figures/draw_schematics.py --font-root /path/to/texmf-dist/fonts --out /absolute/fresh/diagrams
python plots/draw_sqlite_costs.py --font-root /path/to/texmf-dist/fonts --out /absolute/fresh/sqlite-plots
```

The SQLite plot reads all 48 primary cost rows and uses ratios of policy medians,
the same statistic as its table. Solid and outlined bars distinguish journal
modes, with aligned values outside the plotting area. The write-ratio plot in
`plots/draw_write_ratios.py` reads the primary scaling rows
and uses medians of per-input ratios. Neither renderer runs an experiment.

Core experiments use Python 3.10 or later and the standard library, on a POSIX host with `resource` and process support. The recorded reproduction used Ubuntu 24.04 and Python 3.12. Complete SMT comparisons additionally require a usable local Z3 C shared library, discoverable by `ctypes.util.find_library`, or a local `z3-solver` installation. The campaign uses included inputs; TeX is unnecessary for this standalone repository.

## Reproduce the evidence

From this directory:

```sh
python scripts/run_tests.py -v
python scripts/reproduce_portable.py --require-smt
python scripts/aggregate.py
python scripts/run_posix_smoke.py
python scripts/validate_release.py
```

The full campaign runs seven sequential bounded stages: tests, pilots, census, retained variants, fallback, scaling, and public bytes. The POSIX illustration is an additional explicit command. The campaign records per-stage CPU, wall time and process peak RSS. The single-worker default is intentional; do not replace it with unbounded parallel runs.

For a standard-library-only run, in a separate extraction so that the full results are not overwritten:

```sh
python scripts/reproduce_portable.py --without-smt
python scripts/aggregate.py
python scripts/run_posix_smoke.py
python scripts/validate_release.py
```

This requests every non-SMT stage and records omitted solver queries and the solver-dependent unit-test skip. `--require-smt` fails before the campaign if the actual C API is unavailable. Query-budget exhaustion in the bounded fallback analyzer is `unknown`, not `safe`.

## Certificate interface

Current producer and code-separated consumer independently keep a running
same-slot maximum after collecting last users over the complete trace. Each
writer is checked before its own DATA allocation updates the maximum; FREEs
never reset it. This removes repeated earlier-allocation scans, not publication
searches, byte/digest validation, closure storage or minimum-cut pair enumeration.
Both producer and pair analysis compute a greatest-root summary in one
topological pass; the separate consumer computes its own summary. Portable
regressions are included by `scripts/run_tests.py` discovery and CI:

```sh
python -B -m unittest discover -s tests -p test_slot_frontier.py -v
```

This command needs only current included sources/fixtures and the standard
library. Slot checks compare a dense pair
definition, the unchanged pair baseline and bounded concrete all-cut recovery.

```sh
python src/cli.py certify inputs/pilot-trace.json --minimum --certificate certificate.json
python src/cli.py verify inputs/pilot-trace.json certificate.json
```

The certificate can be removed after inspection. The consumer reparses the original trace and validates certificate coverage and witnesses. It does not import the producer, engine, oracle or solver. It was developed within the same project and is not an independent-team implementation or a machine proof. `docs/formats.md` specifies canonical maximum-root witnesses, controlled CLI statuses, all wire formats, and the separate descriptor-plus-raw-payload cost model; strict JSON rejects duplicate keys and nonfinite numbers.

## Paired analysis costs

```sh
python -B scripts/analysis_costs.py --verify
python -B scripts/analysis_costs.py --run
```

Run the second command only in a separate extraction on Linux when intentionally
creating new host measurements. It reuses 45 batch-four scaling histories,
pins one guest CPU, and alternates admission, frontier, pair and consumer order.
Each routine has one warm-up; calibration doubles the call batch to at least
20 ms of CPU time before eight measured batches. The retained 1,440 blocks
include raw elapsed CPU nanoseconds, actual call counts, input identity,
configuration, host information and source bindings. This handles coarse CPU
clocks without interpreting zero-duration calls as a speedup.

`results/analysis-costs/` contains the primary rows and derived summary. Cell
values are medians of eight per-input batch-normalized observations, then of
five input medians. Frontier includes positive-certificate construction and
serialization; pair provides decisions and diagnostics. Admission is separate,
while consumer time includes its own reparsing and checks. The pair routine is
cheaper on smaller cases; obligation reduction is not a universal runtime gain.
The build verifies these bindings before deriving its table, and CI verifies
the retained measurements without replacing them with runner-host timings.

## What to check

The supplied full result contains 33,792 dependency graphs and 2,129,920 candidate event sets; 3,072 graphs are safe and 30,720 unsafe graphs have oracle-checked minimum witnesses. The regression set contains 256 variants (75 safe, 181 unsafe). Fallback tests cover 255 formulas and 2,040 assignments plus 92 fixed-candidate formulas. Scaling has 45 base inputs and 180 policy report rows, with at most 5,599 events. All 720 recovery images pass selected-root, prefix, and specification-byte comparisons and account for 5,382 unreachable occupied-slot observations. The campaign executes 65 evaluation SMT queries plus two pilot queries, with zero unknowns, and all 132 unit methods pass with no skips. The POSIX fixture has eleven agreeing selector projections, zero ordered failures, and two expected unsafe failures.

`check_scientific_invariants.py` checks exact JSON paths and cross-checks primary row counts, decisions, minimum-witness flags, certificate acceptance and byte arithmetic. It does not accept a matching number found elsewhere in a JSON tree. Assertions are ordinary executable checks, not proof-assistant verification.

The Ubuntu primary campaign checks specification bytes, multiplicities,
repeated recovery and quiescent reclamation after each timed recovery call.
The recovery-subclass results separately retain 45 full-length traces and 765
local image-file roundtrips from the same Linux host; these serialization checks
do not exercise filesystem crash persistence.

The test count must agree between the primary runner output and its summary.
The supplied primary execution and current discovered artifact suite both
contain 132 passing methods.
`run_all.py` shares one 45-minute wall-time
deadline across all seven stages. The prepared scientific-checks workflow runs
from this flat artifact root on Ubuntu 24.04 with a 40-minute scientific-command
deadline, a 45-minute job cap, resource limits, mandatory SMT preflight, and raw
output uploads even after failure.

To compare a same-mode clean rerun against another extracted artifact directory:

```sh
python scripts/validate_release.py --compare /path/to/other/artifact
```

All 17 primary result files are structurally compared; only an explicit list of host timing and resource fields and the unittest elapsed-time text are normalized. Input, source, script and test files are compared byte-for-byte without retaining a release hash manifest. Counts, decisions, witness identities, byte costs and solver statuses are not normalized. Full and core-only runs are intentionally not declared interchangeable.

## Interpretation and limitations

The input directory named `heldout` is retained for interface stability. Its cases have been revisited during repairs and are **regression inputs, not an untouched test set**. The algorithms are not trained statistical models. Generated sample counts do not establish production-workload breadth. Six SQLite header files are three identical pairs across the selected tags; they are exact public bytes, not filesystem access traces.

Certified and unchecked batch-four deduplication are two reports of the same storage trace, not two independent storage engines. The certificate adds offline analysis, not extra durable writes. Chunk savings must be read together with encoded metadata costs. Python validation timings are descriptive host measurements, not device performance or speedup confidence intervals.

No concurrent publication/cleaning, torn writes, distributed faults, generation wraparound, root-history truncation, indefinite snapshot retention, or crash-during-mutating-recovery claim is made. SHA-256 is the implementation identifier; the mathematical model uses exact content identity. See `docs/theorems.md` and `docs/trust-boundaries.md`.

## Contents and provenance

`src/`: model, analyzer, separate consumer, recovery, oracle, solver, engine and POSIX illustration. `scripts/`: bounded experiments and checks. `tests/`: regressions. `inputs/`: exact public bytes and deterministic configurations/cases. `results/`: primary outputs and derived summaries. `docs/`: model/format/claim documentation. `claim_evidence_ledger.csv`: scientific claim-to-evidence map. `external_resources.csv`, `PROVENANCE.md`, and `licenses/`: source attribution.

Small-domain recovery compares bytes with the offline oracle. The 720-image
campaign also compares every selected-root namespace with its specification,
alongside multiplicity, repeated recovery, and reclamation checks.

## Full-history recovery and publication-valid fallback

```sh
python -B scripts/run_tests.py --pattern test_recovery_subclasses.py -v
python -B scripts/check_recovery_subclasses.py
python -B src/cli.py fallback-publication inputs/pilot-trace.json
```

The publication-valid checker first checks P, then uses a deletion worklist to
decide universal acknowledgement safety for an unbounded candidate set. Failure
of P returns `not-applicable`, safe=null, exit code 2; general fallback still uses
the separate fixed-candidate checker. The polynomial checker returns a greatest
violating cut, never a minimum-cardinality claim. Its proof is in
`docs/theorems.md` and its graph/byte-oracle rows are in
`results/recovery-subclasses/publication-fallback.jsonl.gz`.

The recovery sampler now splits its nonempty budget between evenly spaced root
and event issue ranks over the complete history. It excludes the separately
measured full cut and fills duplicate closures explicitly. New rows record exact
cut event identifiers, selected prefix, and maximum event. `reproduce.py` also
compares the selected root, prefix and recovered bytes with the specification.
The prior 16-cut sampler exhausted its budget at the empty cut and first fifteen
roots, reaching prefix 60 at batch four or 15 at batch one in a 512-operation
history. The supplied 720-image campaign uses stratified cuts and records their
current recovery timings and occupancy totals.

The separate local check examines 512-operation histories for all three workload
families, five seeds and three physical policies. It stores real local image-file
roundtrips and abstract recovery checks in `results/recovery-subclasses/`, not new
POSIX persistence observations. The directed regression erases a selected
manifest only at prefixes >=400 to test sampling sensitivity; this is an injected
image fault, not a counterexample to the unmodified safe generated trace.

The executed local check examines 33,792 graphs, retaining 6,528 satisfying P,
and materializes 64,458 legal fallback images. Its 19,200 acknowledgement checks
(9,984 unsafe) match byte enumeration and the union defining the greatest fault
cut. The 45 full-length histories pass all original acknowledgement checks and
produce 765 verified local image-file roundtrips. All four prefix quarters are
represented, and each proper-cut set reaches prefix 512. The directed late fault
is missed by old cuts and detected in four stratified cuts. Ten focused unit
methods pass, with no skips. `summary.json`, primary JSONL rows and
`unit-tests.json` retain these observations separately from the seven-stage primary campaign.

## Directed contract checks

```sh
python scripts/check_targeted.py
```

The explicit fixtures in `tests/targeted_cases.py` bypass the workload generator.
They cover forward manifest/chunk bindings, truly missing allocations, multiple
acknowledgements and budgets, canonical witnesses with several qualifying roots,
and non-string role/kind inputs through the CLI. See `docs/contract-validation.md`
for the contract definitions and measurement provenance.

## Plot retained serialized-write observations

The optional Python renderer reads the 512-operation records in
`results/scaling/policy-metrics.jsonl`, verifies the same five seeds per policy,
and draws the nine rounded medians used in the paper. Ratios are serialized
write bytes divided by logical input bytes. Certified and unchecked batch-four
traces share the same storage results.

```sh
python -m pip install -r requirements-figures.txt
# Choose an absent or empty output directory; do not use the shipped figures/ directory.
python plots/draw_write_ratios.py --font-root /path/to/texmf-dist/fonts --output-dir /absolute/fresh/retirement-write-figures
python plots/draw_sqlite_costs.py --font-root /path/to/texmf-dist/fonts --out /absolute/fresh/retirement-write-figures
python figures/draw_schematics.py --font-root /path/to/texmf-dist/fonts --out /absolute/fresh/retirement-write-figures
python -B -m unittest discover -s tests -p test_plot_sources.py -v
```

Rendering uses the optional ReportLab dependency declared separately from the
standard-library core and the external Linux Libertine Type 1 font from
a TeX distribution. The output is an embedded-font vector PDF; rendering does
not execute experiments or replace the retained observations. Choose an empty
output directory. `--input` and `--column-width-pt` can select another validated
input file and the manuscript's actual TeX column width.

The four manuscript figures are all drawn in Python, without a TeX graphics
engine. The write-cost chart uses medians of five per-input ratios. The SQLite
chart instead divides matched policy medians and displays paired horizontal
bars with a common equal-cost reference. Their different aggregation rules
follow the corresponding tables. The two symbolic diagrams preserve the
ordering and recovery interfaces; their renderer checks label clearance,
minimum node padding and both connector and arrowhead intersections before
writing the vector PDFs.

## License

Original project code is available under the MIT License. Notices for third-party
inputs and dependencies are retained in `PROVENANCE.md` and `licenses/`.
