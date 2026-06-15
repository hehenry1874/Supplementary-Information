# Full near-port satellite proxy panel — end-to-end workflow

All outputs are **proxies** in standardized observation windows (not legal port boundaries, not true berth area). Do not merge into readiness scores in this phase.

## Phase 1 — Google Earth Engine (you run in Code Editor)

1. **Asset:** Upload / confirm `ports_wpi_full_368` with `port_id`, `port_name` or `port_name_standard`, `country`, Point geometry.
2. **Script:** `gee/port_physical_expansion_wpi_full_safe.js`
3. Set `PORTS_ASSET_ID`, `INCLUDE_2026_PARTIAL = true` (default).
4. **Batches** (50 ports each; last batch 18 ports):


| BATCH_START | BATCH_SIZE | EXPORT_SUFFIX    | Expected rows (×5 years ×2 buffers) |
| ----------- | ---------- | ---------------- | ----------------------------------- |
| 0           | 50         | batch000_050     | 500                                 |
| 50          | 50         | batch050_100     | 500                                 |
| 100         | 50         | batch100_150     | 500                                 |
| 150         | 50         | batch150_200     | 500                                 |
| 200         | 50         | batch200_250     | 500                                 |
| 250         | 50         | batch250_300     | 500                                 |
| 300         | 50         | batch300_350     | 500                                 |
| **350**     | **18**     | **batch350_368** | **180**                             |


1. Each run: **Run** script → **Tasks** → run export. Drive description pattern:
  `port_satellite_scale_panel_<EXPORT_SUFFIX>_with2026`
2. Download every CSV into repo folder `**gee/exports/`** (keep original names or rename consistently; default merge uses `*.csv`).

**2026:** Rows have `partial_calendar_year_flag = 1` and `satellite_year_type = partial_year`. Treat as **not fully comparable** to full calendar years in interpretation.

---

## Phase 2–6 — Local (automated)

From repo root (after all batch CSVs are in `gee/exports/`):

```powershell
python scripts/PortSatellite-Full-Panel-Pipeline.py all --exports-dir gee/exports --n-ports 368 --n-years 5
```

Subcommands:

- `merge` — concatenate CSVs, save `port_satellite_scale_panel_full_merged_before_sentinel.csv`, replace `-999` with NaN → `gee/port_satellite_scale_panel_full.csv`
- `process` — 5 km / 10 km / main 5km split; change summaries; preliminary index
- `qc` — summary + anomalies + manual checklist

### Generated files (under `gee/`)


| File                                                         | Purpose                                                        |
| ------------------------------------------------------------ | -------------------------------------------------------------- |
| `port_satellite_scale_panel_full.csv`                        | Full long panel (cleaned numerics)                             |
| `port_satellite_scale_panel_full_merged_before_sentinel.csv` | Pre–NaN backup                                                 |
| `port_satellite_scale_panel_full_5km.csv`                    | Primary buffer                                                 |
| `port_satellite_scale_panel_full_10km.csv`                   | Robustness buffer                                              |
| `port_satellite_scale_panel_full_main5km_analysis.csv`       | Copy of 5 km (main analysis)                                   |
| `port_satellite_change_summary_2015_2024.csv`                | Port×buffer 2015–2024 aggregates                               |
| `port_satellite_change_summary_2018_2024.csv`                | Port×buffer 2018–2024 robustness                               |
| `physical_expansion_index_prelim.csv`                        | Winsorized min–max index (2015 & 2018 baselines @ y2024, 5 km) |
| `port_satellite_field_completeness.csv`                      | Required column non-null %                                     |
| `port_satellite_full_qc_summary.csv`                         | Row counts, flag distributions                                 |
| `port_satellite_anomaly_list.csv`                            | Automated anomalies                                            |
| `port_satellite_manual_check_list.csv`                       | Union of QC rules for visual review                            |


If a non-batch file sits in `gee/exports/`, narrow the pattern, e.g.:

```powershell
python scripts/PortSatellite-Full-Panel-Pipeline.py merge --exports-dir gee/exports --pattern "port_satellite_scale_panel_batch*_with2026.csv"
```

---

## Phase 7 — Do **not** do yet

- Do not append to `readiness_panel_score_v1.csv`
- No PLSCI/CPPI regression
- Do not interpret as true port area; keep **proxy** language in papers

---

## First run without 2026 (optional)

In the GEE script set `INCLUDE_2026_PARTIAL = false` → `EXPORT_YEAR_TAG` becomes `no2026`, YEARS drop 2026. Then run pipeline with `--n-years 4` and expect **2944** rows for 368 ports.