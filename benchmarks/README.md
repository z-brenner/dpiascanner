# benchmarks

**Inputs:** the fixtures and their manifests. **Outputs:** `results/*.json` and `RESULTS.md`.

- `run_benchmark.py` runs the full pipeline (analysis, classification, findings) on every
  fixture with the stub provider, and with Jev when `TYPESAFE_API_KEY` and
  `TYPESAFE_BASE_URL` are set. `--check` is the CI gate (`make benchmark-gate`): 100 percent
  recall on the canary fixtures and zero false positives on clean-python.
- `llm_baseline.py` gives a model the concatenated repository (prompt in
  `prompts/llm_baseline.md`) and scores its JSON answer the same way. It needs
  `ANTHROPIC_API_KEY`; without one it records the baseline as not run. Raw replies are saved
  for audit and `--replay`.
- `scoring.py` holds the shared matching rules and metrics.

The LLM never sees `MANIFEST.yaml`, READMEs, `lantern.yml`, tests, or the fixtures'
exercise drivers; `tests/test_scoring.py` and the prompt builder's exclusions keep it that
way.
