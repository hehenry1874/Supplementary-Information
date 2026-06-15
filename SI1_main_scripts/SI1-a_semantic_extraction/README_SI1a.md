# SI1-a Semantic Extraction

## Purpose

This folder documents the semantic evidence pipeline used to transform web/PDF sources into readiness evidence, year-coded panels, and readiness scores. It contains scripts only.

## Contents

- `scripts/`: portable copies of the core pipeline scripts from `scripts/LLM_core/`.

## Script sequence

Run from the `scripts/` subfolder if rerunning:

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

## Data availability

The scripts expect data files in a sibling working directory, including:

- `double_batch_ports_master.csv`: stable port master and `port_id` key.
- `candidate_sources.csv`: web-source candidate inventory.
- `manual_url_supplement.csv`: manually added source URLs.
- `evidence_wide_combined.csv`: combined LLM evidence table.
- `evidence_long.csv`: variable-level evidence records after year coding.
- `future_targets.csv`: future target claims extracted from the evidence.
- `readiness_panel_raw.csv`: 2015-2026 port-year raw stage panel.
- `readiness_panel_score_v1.csv`: scored readiness panel used downstream.

These data files are not bundled in this supplement. They are available from the corresponding author on reasonable request. The corpus text files are distributed separately as SI2.

## Caveats

- Rerunning LLM extraction requires `XAI_API_KEY` and may produce small stochastic/API-version differences.
- The effective year is taken from variable-level evidence year fields when available; otherwise the pipeline falls back to the document/access year.
- Failed or empty captures are excluded from the distributed corpus archive unless separately supplied.
