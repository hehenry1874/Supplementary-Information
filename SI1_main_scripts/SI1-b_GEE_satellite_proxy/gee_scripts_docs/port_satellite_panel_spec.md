# Port satellite scale panel — output schema and QA

This document accompanies `gee/port_physical_expansion_wpi.js`. **Buffers are standardized observation windows centered on WPI (or harmonized) points — not legal or operational port boundaries.**

## Input linkage (`ports_wpi.csv`)

Your repository file `scripts/ports_wpi.csv` uses: `INDEX_NO`, `PORT_NAME`, `COUNTRY`, `LATITUDE`, `LONGITUDE`. For merges with the PLSCI panel, `**port_id` should be your analysis key** (e.g. `PLSCI_000049`), with coordinates taken from the WPI row that best matches the port. Use `scripts/Prepare-GEE-Pilot-Ports.py` to build a GeoJSON/CSV for Earth Engine upload (`port_id`, `port_name`, `country`, `lon`, `lat`, optional `wpi_index_no`).

## Output table: `port_satellite_scale_panel.csv`

One row per **port × calendar_year × buffer_radius_km**. Primary analysis: `**buffer_radius_km = 5`**. Robustness: `**10`**.


| Field                                | Type      | Description                                                                                                                            |
| ------------------------------------ | --------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `port_id`                            | string    | Panel key (e.g. PLSCI / study id).                                                                                                     |
| `port_name`                          | string    | Human-readable name.                                                                                                                   |
| `country`                            | string    | Country label aligned with panel.                                                                                                      |
| `calendar_year`                      | int       | Year node (`2015`, `2018`, `2021`, `2024`, `2026`).                                                                                    |
| `buffer_radius_km`                   | float/int | 5 or 10.                                                                                                                               |
| `analysis_scale_m`                   | int       | Pixel size used for zonal stats (10).                                                                                                  |
| `observation_window_label`           | string    | Fixed text: clarifies circle is not a port boundary.                                                                                   |
| **Built-up / impervious proxy**      |           |                                                                                                                                        |
| `builtup_area_km2`                   | float     | Area meeting built/impervious rules (see logic below).                                                                                 |
| `builtup_share`                      | float     | `builtup_area_km2 / aoi_area_km2`.                                                                                                     |
| `builtup_change_from_2015_km2`       | float     | Vs. same buffer, 2015 composite.                                                                                                       |
| **Water → land / reclamation proxy** |           |                                                                                                                                        |
| `water_to_land_area_km2`             | float     | 2015 water mask ∩ target-year “land-like built” mask.                                                                                  |
| `reclamation_proxy_flag`             | int       | 1 if `water_to_land_area_km2` > threshold (default 0.01 km²).                                                                          |
| `shoreline_change_proxy_km2`         | float     | Area where annual water mask shows loss vs 2015 S2 water (land gain proxy).                                                            |
| **Yard-like open paved proxy**       |           |                                                                                                                                        |
| `yard_like_area_km2`                 | float     | Low NDVI, non-water, bare/built-like spectral/DW rule (not container-specific).                                                        |
| `yard_like_share`                    | float     | `yard_like_area_km2 / aoi_area_km2`.                                                                                                   |
| `yard_like_change_from_2015_km2`     | float     | Vs. 2015.                                                                                                                              |
| `yard_like_patch_count`              | int       | Distinct connected components (8-neighbor grouping at analysis scale).                                                                 |
| `largest_yard_like_patch_km2`        | float     | Largest component area (approx., from max pixel count × pixel size).                                                                   |
| **AOI & quality**                    |           |                                                                                                                                        |
| `aoi_area_km2`                       | float     | Total window area (disk).                                                                                                              |
| `s2_scene_count`                     | int       | Scenes entering annual S2 median after QA / cloud prescreen.                                                                           |
| `dw_image_count`                     | int       | Dynamic World images in year after filterBounds.                                                                                       |
| `image_quality_flag`                 | int       | 0 = pass (S2 + DW counts); 1 = elevated risk of instability.                                                                           |
| `cloud_quality_flag`                 | int       | 0 = enough S2 scenes after prescreen; 1 = sparse.                                                                                      |
| `wpi_location_check_flag`            | int       | 0 = no heuristic alert; 2 = water-dominated; 3 = vegetation-dominated low built; 4 = very low built & low water. **Not ground truth.** |
| `partial_calendar_year_flag`         | int       | 1 for `2026` (incomplete calendar year in operational use).                                                                            |


## Indicator logic (summary)

### 1) Built-up / impervious expansion

- **Dynamic World**: annual **median** per-pixel `built` probability (no single-date image).
- **Sentinel-2**: annual **median** composite of `NDVI`, `NDWI`, `MNDWI`, `NDBI` after cloud/cirrus QA on L2A harmonized collection; MOS prefilter on scene-level cloud metadata.
- **Built proxy mask**: `built_prob > T_dw` **OR** (`NDVI` very low **and** `NDBI` elevated). This is **not** shore power, PV, or substation detection — coarse sealed-surface signal only.
- **Zonal stats**: sum of masked pixel areas at **10 m**; shares relative to disk AOI.

### 2) Reclamation / shoreline proxy

- **2015 water baseline**: union of (a) S2 annual median **MNDWI** water mask and (b) **JRC GSW occurrence ≥ 80** (long-run open water) clipped to AOI — reduces missing water when one layer mis-fires.
- **Target-year “land-like”**: non-water (`MNDWI` below loose threshold) **and** built proxy true (hardened surface proxy).
- `**water_to_land_area_km2`**: intersection of 2015 water baseline and target land-like.
- `**shoreline_change_proxy_km2`**: area where **S2** water mask in year *t* is **lower** than in **2015** (simple land-gain-from-water proxy). **Not** sub-pixel shoreline survey accuracy.

### 3) Yard-like open paved area

- Heuristic **commodity inspection yard / stacking plain** style mask: low `NDVI`, not water, and (`built_prob` modest **or** `NDBI` > 0). **Excludes** fine facility typing (no detection of individual cranes, plugs, arrays).
- **Patches**: connected components on binary mask; **count** distinct labels; **largest** patch from `connectedPixelCount` max × pixel area (approximation).

### Quality flags (interpretation)


| Flag value                | Meaning                                                                   |
| ------------------------- | ------------------------------------------------------------------------- |
| `image_quality_flag = 0`  | S2 scene count ≥ 8 **and** DW count ≥ 4 (tunable).                        |
| `cloud_quality_flag = 0`  | S2 scene count ≥ 8.                                                       |
| `wpi_location_check_flag` | Heuristic mis-point / land-cover mismatch alerts only; validate visually. |


## Known limitations / hard-to-implement items (be explicit in papers)

1. **Dynamic World** coverage begins **mid-2015**; early-2015 annual medians may be noisier — consider sensitivity excluding 2015 or using JRC + Landsat-only supplemental masks (commented extension).
2. **JRC GSW** is a **longevity** product; it is **not** a year-specific hydrography chart. Use it as **structural** water prior, not as yearly bathymetry.
3. **Annual median** reduces clouds but **does not remove** all ambiguity in monsoon / high-latitude ports — use flags and robustness `10 km` window.
4. **Mixed materials**: bare soil, dry stacks, and **some** rooftops enter built/yard proxies — this is intentional for a **physical expansion** proxy, not land-use classification.
5. **2026** node should be treated as **partial-year** unless you restrict the date filter (e.g. full-year run in 2027).
6. **Global batch runtime**: full-panel `reduceRegion` per row is heavy; for all ports use **batch export**, `tileScale`, and consider **sharding** by continent or `buffer_radius_km` split.
7. **Landsat**: requested for spectral indices; the reference script prioritizes **S2 + DW** for 10 m coherence. Add Landsat 8/9 annual median as a **separate band** if you need 2015(early) continuity or cross-sensor QA.

## Pilot validation checklist (30 ports)

1. **Upload QC**: every point plots **on water or quay-side** in Map; note any `wpi_location_check_flag > 0`.
2. **Cloud QC**: histogram `s2_scene_count` / `dw_image_count`; flag ports with `image_quality_flag = 1` for manual review.
3. **Face validity — built-up**: compare `builtup_share` ordering vs visual basemap for mega-ports vs small fishing harbors.
4. **Reclamation**: for known reclamation cases (e.g. visible landfill), check `reclamation_proxy_flag` and **direction** of `water_to_land_area_km2` vs news/GIS layers.
5. **Shoreline proxy**: confirm sign of `shoreline_change_proxy_km2` against tidal flats / estuaries (expect noise in intertidal systems).
6. **Yard-like**: inspect largest patch: does it align with **container stacks / pavements** vs airport / urban core leakage? If urban leakage, tighten rules (e.g. distance-to-shore mask) in a **sensitivity** branch only.
7. **Buffer robustness**: correlate 5 km vs 10 km deltas — high divergence implies **scale sensitivity** (urban adjacency or delta geomorphology).
8. **Year alignment**: document that composites are **calendar-year** medians, not fiscal-year port statistics.

## Postprocessing for correlation analysis

- Merge on `(port_id, calendar_year)` to text readiness panel; for readiness indices centered on *document* years, use explicit merge rules (e.g. nearest prior satellite year).
- Keep `**buffer_radius_km`** as a column; do not collapse 5 km and 10 km without a modeling choice.