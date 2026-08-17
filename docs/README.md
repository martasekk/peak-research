# docs/

Historical and reference material. Nothing here is loaded by the skill or the commands — it
sits outside `skills/` deliberately so it never consumes context during a run.

- **`CHANGELOG.md`** — every behavioural change, most recent first. `test_gate.py` carries a
  regression test for each entry in the correctness pass.
- **`MODEL_BENCHMARK.md`** — measured comparison of the NVIDIA nemotron-3 models on both
  adjudication tasks. It is why the default chain leads with the 120B rather than the 550B,
  and why the 30B "lightning" model is excluded. Re-run it before trusting the ordering
  after a provider-side model revision.
- **`COMPARISON.md`** — how this method reached its current shape: the 20-stage-file layout it
  replaced, and the measured before/after of the improvement pass (2 retrieval passes → 4,
  6 records → 16 on the same topic, G5 added). Its references to `peak-research/` and
  `peak-research-v2/` as separate on-disk skills describe that history, not this package.
- **`baseline/`** — the pre-hardening snapshot the improvement pass was measured against.
  Reference only: it predates the cache, the four-pass retrieval, and the G5 gate. Do not run
  it and do not import from it.
