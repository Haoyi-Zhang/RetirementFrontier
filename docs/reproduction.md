# Reproduction and observation provenance

The supplied primary results come from a completed seven-stage run on Ubuntu
24.04 with Python 3.12 and Z3. `results/campaign-run.json` records each executed
stage; `results/pilots/unit-tests.json` records 132 passing methods and no skips.
The census, retained variants, fallback, scaling, public-byte rows, and derived
summary belong to that same run.

Recovery uses deterministic full-history stratification. All 720 primary images
agree on selected root, operation prefix, and specification object bytes, and
contain 5,382 unreachable-slot observations. Recovery timing medians/ranges and
scaling process RSS are retained in `results/summary/summary.json` and
`results/scaling/summary.json`; the manuscript adopts these through its explicit
measurement refresh. These are host observations, not device latency
measurements. Specification comparisons occur after the timed calls.

The paired routine-cost campaign is separate: `scripts/analysis_costs.py`
records eight calibrated CPU batches for each of four routines on 45 existing
scaling inputs. It includes actual batch call counts and a source-bound summary
under `results/analysis-costs/`. Use `--verify` to check retained rows and
`--run` only to intentionally replace them with a new Linux-host measurement.
Both analysis implementations include the same greatest-root preprocessing.
Admission is separate; the consumer reparses its input. Small-case pair costs
and the positive-certificate producer costs must be interpreted with these
different outputs visible.

The separate POSIX fixture records eleven completed-helper observations, zero
ordered failures, two expected unsafe failures, and agreement with the abstract
selector projection. Publication-valid fallback and 512-operation local-file
campaigns have their own primary rows under `results/recovery-subclasses/`.

Run from the repository root:

```sh
python scripts/check_targeted.py
python scripts/run_tests.py -v
python scripts/reproduce_portable.py --require-smt
python scripts/aggregate.py
python scripts/run_posix_smoke.py
python scripts/check_recovery_subclasses.py
python scripts/validate_release.py
```

The complete reproduction deliberately creates fresh host observations. Run it
in a separate extraction to preserve the supplied measurement samples. Use
`--without-smt` in place of `--require-smt` for a core-only campaign; the mode and
omissions are explicit. To compare same-mode extractions, use
`python scripts/validate_release.py --compare /path/to/other/artifact`.

The separately stored directed before-edit observations are necessary
counterexample evidence, not previous reviewer opinions or current expected
results. `scripts/check_targeted.py` regenerates current observations, and the
regression suite checks the repaired contracts.
