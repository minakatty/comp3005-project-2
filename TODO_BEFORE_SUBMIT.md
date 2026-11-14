# Before each checkpoint

Everything the spec asks for is in the repository: code, tests, every DESIGN / REPORT
document, sources, assumptions and the design log. What is left is yours alone:

- [ ] Check the name and student number at the top of `README.md`.
- [ ] Run `python -m unittest discover -s tests` (expect `OK`, about 210 tests, ~2.5 min).
- [ ] Prepare for the oral check. For each module read its DESIGN_M*.md next to the code
      and be able to explain any function and any number in its REPORT_M*.md:
      M1 `storage/page.py`, `bufferpool.py`, `heap.py`; M2 `execution/operators.py`,
      `memory.py`; M3 `sqlfront/parser.py`, `translate.py`; M4 `optimizer/estimate.py`,
      `logical.py`, `physical.py`.
- [ ] Skim the two sources listed for the module (the oral check draws one question from the reading).
- [ ] Tag and push the checkpoint: `git tag -f m2` (m3, m4, final …), `git push -f --tags`.

Useful reruns (results are deterministic):

```
python tools/make_traces.py traces
python tools/m1_measure.py --traces traces --out results_m1
python tools/m2_measure.py --out results_m2                       # ~10 min
python tools/make_m4_dataset.py --db db_m4 --workload workload_m4
python tools/m4_measure.py --db db_m4 --workload workload_m4 --frames 4 --analyze --out results_m4   # ~30 min
python samples/make_sample_db.py samples_db
python exec.py run --db samples_db --plan samples/plans/11_hash_join.json --frames 4 --json
```
