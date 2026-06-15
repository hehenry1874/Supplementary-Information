# SI1-b GEE Satellite Proxy

## Purpose

This folder documents the Google Earth Engine and local post-processing workflow for the near-port physical-expansion proxy. It contains scripts and workflow documentation only.

## Contents

- `gee_scripts_docs/`: Earth Engine JavaScript scripts plus workflow/schema documentation.
- `local_scripts/`: local Python scripts for upload-table preparation, export merging, QC, and low-confidence reruns.

## Main workflow

1. Build the Earth Engine upload table with `Prepare-GEE-Full368-EE-Table.py`.
2. Upload/confirm the port point asset in Earth Engine.
3. Run `port_physical_expansion_wpi_full_safe.js` in GEE Code Editor across the documented batches and download CSV exports locally.
4. Merge and process exports locally with `PortSatellite-Full-Panel-Pipeline.py`.
5. Use `SelectLowConfidenceSatellitePorts.py` and `port_physical_expansion_wpi_lowconf_supp.js` for supplementary low-confidence reruns where applicable.

## Data availability

The Earth Engine upload tables, raw batch exports, and cleaned satellite proxy panels (CSV and GeoJSON) are not bundled in this supplement. They are available from the corresponding author on reasonable request. Relevant data products include:

- `ports_wpi_full_368_ee_table.csv`: Earth Engine upload table for the 368-port panel.
- `port_satellite_scale_panel_full.csv`: cleaned full satellite proxy panel.
- `port_satellite_scale_panel_full_with_supp.csv`: supplemented satellite proxy panel if low-confidence reruns are incorporated.
- `ports_wpi_low_confidence.geojson`: low-confidence supplementary upload/reference geometry.

## Caveats

- These measures are proxies in standardized WPI-centered buffers, not legal port boundaries or true berth footprints.
- Built-up, reclamation, and yard-like metrics do not directly measure shore power, green fuels, renewables, or terminal equipment.
- 5 km is the primary observation window; 10 km is a robustness window, and low-confidence supplementary scripts may include a wider window.
- 2026 rows are partial-year observations and should not be interpreted as fully comparable to full calendar years.
