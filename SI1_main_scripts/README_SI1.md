# SI1 Main Scripts

This folder contains the main analysis scripts for the study. It is distributed as code only: no data tables are included here.

## Structure

- `SI1-a_semantic_extraction/`: web-source discovery, corpus construction, LLM evidence extraction, evidence merging, year coding, and readiness scoring.
- `SI1-b_GEE_satellite_proxy/`: Google Earth Engine scripts and local processing scripts for the near-port satellite physical-expansion proxy.

## Data availability

To keep this supplement lightweight, input inventories, intermediate tables, and final result tables (CSV and other data files) are not bundled here. They are available from the corresponding author on reasonable request. The corpus text files are distributed separately as SI2.

## Reproducibility note

A complete rerun of semantic extraction requires an XAI-compatible API key (`XAI_API_KEY`). A complete GEE rerun requires a Google Earth Engine account, uploaded port point assets, and manual batch exports.

## Important caveats

- Semantic readiness scores are derived from LLM-extracted evidence stages and explicit year-coding rules.
- Satellite indicators are standardized near-port proxy measurements, not legal port boundaries and not direct observations of port decarbonization infrastructure.
- 2026 satellite observations are partial calendar-year rows if `INCLUDE_2026_PARTIAL = true` in the GEE scripts.
