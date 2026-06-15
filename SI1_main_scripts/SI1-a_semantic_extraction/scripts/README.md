# LLM_core — bundled pipeline scripts

These copies read and write data in the **parent folder** (`scripts/` by default): `corpus/`, `candidate_sources.csv`, `evidence_wide*.csv`, `readiness_panel_raw.csv`, etc. Keep `**llm_core_paths.py`** next to the other `.py` files in this directory.

## Suggested run order (from this folder)

```text
set PYTHONPATH=%CD%
python Build-Corpus.py --with-manual-urls
python Build-Corpus-Remedial.py --max-workers 6
python Extract-Evidence-Wide.py
python Extract-Evidence-Pass2.py
python Merge-Evidence.py
python Execute-YearCoding.py
python Build-ReadinessScoresV1.py
python reconcile_wide_vs_panel.py
```

- Set `XAI_API_KEY` (and optionally `XAI_MODEL`) before Extract steps.
- `corpus_qc_refresh.py` is optional QC / refetch helper.

## Regenerate these copies from project `scripts/`

```text
cd ..
python pack_llm_core.py
```

## Hollow corpus

Scripts that failed QC (fetch errors, tiny body, etc.) may be moved to `scripts/corpus_fail/` with `move_hollow_corpus_to_fail.py` (lives in parent `scripts/`, not in this bundle).