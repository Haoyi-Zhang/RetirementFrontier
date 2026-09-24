# Reproduction record

The full campaign and a separately copied core-only campaign were executed successfully. Full mode executes 67 scientific SMT calls and 55 passing test methods. Core mode executes zero SMT calls, 54 passing methods, and one explicitly skipped solver-dependent method. Both modes pass the 66 exact-path/primary-row consistency checks. A skipped solver check is not counted as a pass.

## Clean archive reconstruction

A ZIP of the current scientific tree was extracted into a new directory. In that extraction, the full seven-stage campaign, aggregation, POSIX illustration, 55 test methods, exact result validation, certificate CLI round trip, and manuscript build all returned zero. The comparison found identical logical content in all 17 primary result files and byte-for-byte identical `inputs/`, `src/`, `scripts/`, and `tests/` trees. The rebuilt 25-page PDF had identical page-by-page extracted text to the canonical manuscript. Commands and outcomes are recorded in `reproduction-check.json`.

The raw canonical full observations are in `results/campaign-run.json`, with mode in `results/summary/execution-mode.json`. Primary JSONL rows and stage summaries are retained. This is same-project reproduction in clean directories, not independent authorship, external artifact evaluation, or a machine proof.

## Comparison contract

The validator's 17-file comparison preserves logical decisions, minimum witnesses, byte costs, certificate content, and counts. Only the host fields explicitly enumerated in `scripts/validate_release.py` and the unittest elapsed-time field may differ. Stable scientific input filenames and wire-format identifiers are not manuscript version history. Core-only and full results are validated separately, never silently declared equivalent.

The manuscript retains its reported canonical host observations during an ordinary rebuild. To intentionally adopt observations from a new run, execute `python build.py --refresh-measurements` in `paper/` and review the changed text. Timing noise is not a scientific correctness result.
