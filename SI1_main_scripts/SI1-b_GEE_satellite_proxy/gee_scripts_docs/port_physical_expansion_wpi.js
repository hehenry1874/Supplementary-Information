/**
 * Port physical expansion proxy — Google Earth Engine SAFE PILOT VERSION
 *
 * Standardized observation windows around WPI points.
 * These are NOT official port boundaries.
 *
 * Core indicators:
 * 1. Built-up / impervious expansion proxy
 * 2. Water-to-land / reclamation proxy
 * 3. Yard-like open paved area proxy
 *
 * This version is intentionally defensive:
 * - safe buffer distance handling
 * - empty Sentinel-2 and Dynamic World collection fallback
 * - safe reducer outputs
 * - connectedComponents disabled for pilot stability
 *
 * Recommended first run:
 * - 30 pilot ports
 * - years: 2015, 2018, 2021, 2024
 * - buffers: 5 km and 10 km
 *
 * After this works, set INCLUDE_2026_PARTIAL = true.
 */

// =========================
// CONFIG
// =========================

// For first debug run, keep 2026 off.
// After the pilot exports successfully, set this to true.
var INCLUDE_2026_PARTIAL = false;

var YEARS = [2015, 2018, 2021, 2024];
if (INCLUDE_2026_PARTIAL) {
  YEARS.push(2026);
}

var BUFFER_KM_LIST = [5, 10];

// true = use uploaded pilot table asset
// false = use inline smoke-test points
var USE_PILOT30_UPLOADED_ASSET = true;

// Change this to your own uploaded asset ID.
var PORTS_ASSET_ID = 'projects/my-project-testfor30/assets/ports_wpi_pilot30_ee_table';

var MAX_CLOUD_PRESCREEN_PCT = 60;
var MIN_SCENES_GOOD = 8;
var MIN_DW_IMAGES_GOOD = 4;

// Built-up proxy thresholds
var DW_BUILT_PROB_THRESH = 0.35;
var NDVI_LOW_MAX = 0.12;
var NDBI_BUILT_MIN = 0.05;

// Water thresholds
var MNDWI_WATER_MIN = -0.1;
var MNDWI_WATER_STRICT = 0.05;
var JRC_OCC_WATER_MIN = 80;

// Reclamation flag threshold
var RECLAMATION_AREA_FLAG_KM2 = 0.01;

// Patch metrics are disabled in this safe pilot version.
// Keep placeholder values -999.
var PATCH_METRICS_ENABLED = false;

// =========================
// Inline smoke-test points
// =========================

var PILOT_FEATURES = ee.FeatureCollection([
  ee.Feature(ee.Geometry.Point([-63.13, 46.23]), {
    port_id: '5750',
    port_name: 'CHARLOTTETOWN',
    country: 'CA'
  }),
  ee.Feature(ee.Geometry.Point([39.18, 21.48]), {
    port_id: '48140',
    port_name: 'JIDDAH',
    country: 'SA'
  }),
  ee.Feature(ee.Geometry.Point([4.42, 51.91]), {
    port_id: '59988',
    port_name: 'ROTTERDAM',
    country: 'NL'
  }),
  ee.Feature(ee.Geometry.Point([-118.27, 33.73]), {
    port_id: 'USLAX',
    port_name: 'LOS_ANGELES',
    country: 'US'
  })
]);

// =========================
// Safe helpers
// =========================

function safeNumber(value, defaultValue) {
  return ee.Number(
    ee.Algorithms.If(
      ee.Algorithms.IsEqual(value, null),
      defaultValue,
      value
    )
  );
}

function boolToMask(img) {
  return img.unmask(0).gt(0);
}

// =========================
// Geometry
// =========================

function bufferKm(geom, km) {
  var distanceMeters = ee.Number(km).multiply(1000);
  return geom.buffer(distanceMeters, 30);
}

function totalAoiKm2(aoi) {
  return ee.Number(aoi.area(1)).divide(1e6);
}

// =========================
// Sentinel-2 annual median composite
// =========================

function s2Collection(year, aoi) {
  var y = ee.Number(year);
  var start = ee.Date.fromYMD(y, 1, 1);
  var end = start.advance(1, 'year');

  return ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
    .filterDate(start, end)
    .filterBounds(aoi)
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', MAX_CLOUD_PRESCREEN_PCT));
}

function maskS2CloudsSR(image) {
  var qa = image.select('QA60');
  var cloudBitMask = 1 << 10;
  var cirrusBitMask = 1 << 11;

  var mask = qa.bitwiseAnd(cloudBitMask).eq(0)
    .and(qa.bitwiseAnd(cirrusBitMask).eq(0));

  return image.updateMask(mask).divide(10000);
}

function addSpectralIndices(image) {
  var ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI');
  var ndwi = image.normalizedDifference(['B3', 'B8']).rename('NDWI');
  var mndwi = image.normalizedDifference(['B3', 'B11']).rename('MNDWI');
  var ndbi = image.normalizedDifference(['B11', 'B8']).rename('NDBI');

  return image.addBands([ndvi, ndwi, mndwi, ndbi]);
}

function emptyS2IndexImage(aoi) {
  // Conservative fallback:
  // NDVI=1 prevents false low-vegetation built-up.
  // NDBI=-1 prevents false NDBI built-up.
  // MNDWI=0 prevents strict water detection.
  return ee.Image.cat([
    ee.Image.constant(1).rename('NDVI'),
    ee.Image.constant(0).rename('NDWI'),
    ee.Image.constant(0).rename('MNDWI'),
    ee.Image.constant(-1).rename('NDBI')
  ]).clip(aoi);
}

function annualMedianS2(year, aoi) {
  var col = s2Collection(year, aoi)
    .map(maskS2CloudsSR)
    .map(addSpectralIndices)
    .select(['NDVI', 'NDWI', 'MNDWI', 'NDBI']);

  var n = col.size();

  var safeCol = ee.ImageCollection(
    ee.Algorithms.If(
      n.gt(0),
      col,
      ee.ImageCollection([emptyS2IndexImage(aoi)])
    )
  );

  var median = safeCol.median().clip(aoi);

  return median
    .set('year', year)
    .set('s2_scene_count', n)
    .set('s2_empty_flag', ee.Algorithms.If(n.gt(0), 0, 1));
}

// =========================
// Dynamic World annual median built probability
// =========================

function dwBuiltCollection(year, aoi) {
  var y = ee.Number(year);
  var start = ee.Date.fromYMD(y, 1, 1);
  var end = start.advance(1, 'year');

  return ee.ImageCollection('GOOGLE/DYNAMICWORLD/V1')
    .filterDate(start, end)
    .filterBounds(aoi)
    .select('built');
}

function annualMedianBuiltProb(year, aoi) {
  var col = dwBuiltCollection(year, aoi);
  var n = col.size();

  var fallback = ee.Image.constant(0).rename('built');

  var safeCol = ee.ImageCollection(
    ee.Algorithms.If(
      n.gt(0),
      col,
      ee.ImageCollection([fallback])
    )
  );

  var builtImg = safeCol.median()
    .rename('built_prob')
    .clip(aoi);

  return builtImg
    .set('year', year)
    .set('dw_image_count', n)
    .set('dw_empty_flag', ee.Algorithms.If(n.gt(0), 0, 1));
}

// =========================
// JRC Global Surface Water
// =========================

function jrcWaterReference(aoi) {
  var occ = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence');

  return occ.gte(JRC_OCC_WATER_MIN)
    .unmask(0)
    .clip(aoi)
    .rename('jrc_water');
}

// =========================
// Area calculation
// =========================

function maskAreaKm2(maskImage, aoi, scaleM) {
  var areaImage = maskImage
    .unmask(0)
    .gt(0)
    .rename('m')
    .multiply(ee.Image.pixelArea());

  var stats = areaImage.reduceRegion({
    reducer: ee.Reducer.sum(),
    geometry: aoi,
    scale: scaleM,
    maxPixels: 1e13,
    bestEffort: true,
    tileScale: 4
  });

  return safeNumber(stats.get('m'), 0).divide(1e6);
}

// =========================
// Quality flags
// =========================

function sceneQualityFlags(s2Count, dwCount) {
  var s2c = safeNumber(s2Count, 0);
  var dwc = safeNumber(dwCount, 0);

  var okS2 = s2c.gte(MIN_SCENES_GOOD);
  var okDw = dwc.gte(MIN_DW_IMAGES_GOOD);

  return {
    image_quality_flag: ee.Algorithms.If(
      okS2,
      ee.Algorithms.If(okDw, 0, 1),
      1
    ),
    cloud_quality_flag: ee.Algorithms.If(okS2, 0, 1)
  };
}

/**
 * wpi_location_check_flag:
 * 0 = okay
 * 2 = water-dominated window
 * 3 = vegetation-dominated / sparse built-up
 * 4 = very low built-up and low water, likely point issue
 */
function wpiLocationCheckFlags(s2_2015, aoi, waterMask2015, builtProxy2015) {
  var stats = ee.Image.cat([
    waterMask2015.unmask(0).rename('w'),
    s2_2015.select('NDVI').unmask(0).gt(0.35).rename('v'),
    builtProxy2015.unmask(0).rename('b')
  ]).reduceRegion({
    reducer: ee.Reducer.mean(),
    geometry: aoi,
    scale: 20,
    maxPixels: 1e13,
    bestEffort: true,
    tileScale: 4
  });

  var wMean = safeNumber(stats.get('w'), 0);
  var vMean = safeNumber(stats.get('v'), 0);
  var bMean = safeNumber(stats.get('b'), 0);

  return ee.Algorithms.If(
    wMean.gt(0.85),
    2,
    ee.Algorithms.If(
      vMean.gt(0.55).and(bMean.lt(0.05)),
      3,
      ee.Algorithms.If(
        bMean.lt(0.02).and(wMean.lt(0.2)),
        4,
        0
      )
    )
  );
}

// =========================
// Indicator masks
// =========================

function builtProxyMask(dwBuiltProb, s2y) {
  var dwB = dwBuiltProb.select('built_prob').gt(DW_BUILT_PROB_THRESH);

  var spec = s2y.select('NDVI').lt(NDVI_LOW_MAX)
    .and(s2y.select('NDBI').gt(NDBI_BUILT_MIN));

  return dwB.or(spec)
    .unmask(0)
    .rename('built');
}

function annualWaterMaskS2(s2y) {
  return s2y.select('MNDWI')
    .gt(MNDWI_WATER_STRICT)
    .unmask(0)
    .rename('water');
}

function landLikeMask(dwBuiltProb, s2y) {
  return s2y.select('MNDWI').lte(MNDWI_WATER_MIN)
    .and(builtProxyMask(dwBuiltProb, s2y))
    .unmask(0)
    .rename('landlike');
}

function waterToLandMask(water2015, landLikeT) {
  return water2015.unmask(0)
    .and(landLikeT.unmask(0))
    .rename('w2l');
}

function shorelineLandGainKm2(waterT, water2015, aoi, scaleM) {
  // water in baseline but not water in target year
  var landGain = water2015.unmask(0)
    .and(waterT.unmask(0).not())
    .rename('lg');

  return maskAreaKm2(landGain, aoi, scaleM);
}

function yardLikeMask(dwBuiltProb, s2y) {
  return s2y.select('NDVI').lt(0.18)
    .and(s2y.select('MNDWI').lt(MNDWI_WATER_MIN))
    .and(
      dwBuiltProb.select('built_prob').gt(0.2)
        .or(s2y.select('NDBI').gt(0))
    )
    .unmask(0)
    .rename('yard');
}

// =========================
// Yard patch statistics
// =========================

function yardPatchStats(yardMask, aoi, scaleM) {
  // Pilot-safe version:
  // connectedComponents is disabled to avoid quota/errors.
  // Patch metrics can be restored later after the core panel is stable.
  return {
    patchCount: ee.Number(-999),
    largestKm2: ee.Number(-999)
  };
}

// Optional future replacement after pilot succeeds:
/*
function yardPatchStats(yardMask, aoi, scaleM) {
  var m = yardMask.unmask(0).gt(0).selfMask();

  var sizes = m.connectedPixelCount({
    maxSize: 1024,
    eightConnected: false
  }).rename('patch_px');

  var maxStats = sizes.reduceRegion({
    reducer: ee.Reducer.max(),
    geometry: aoi,
    scale: scaleM,
    maxPixels: 1e13,
    bestEffort: true,
    tileScale: 4
  });

  var maxPx = safeNumber(maxStats.get('patch_px'), 0);

  var largestKm2 = maxPx
    .multiply(ee.Number(scaleM).multiply(scaleM))
    .divide(1e6);

  return {
    patchCount: ee.Number(-999),
    largestKm2: largestKm2
  };
}
*/

// =========================
// One port × year × buffer
// =========================

function analyzePortYearBufferKm(feature, year, kmBuffer) {
  feature = ee.Feature(feature);
  year = ee.Number(year);
  kmBuffer = ee.Number(kmBuffer);

  var geom = feature.geometry();
  var aoi = bufferKm(geom, kmBuffer);

  // Current year images
  var s2y = annualMedianS2(year, aoi);
  var dwY = annualMedianBuiltProb(year, aoi);

  // Baseline 2015 images
  var s2_2015 = annualMedianS2(2015, aoi);
  var dw_2015 = annualMedianBuiltProb(2015, aoi);

  // Baseline water
  var water2015_s2 = annualWaterMaskS2(s2_2015);
  var jrcW = jrcWaterReference(aoi);

  var water2015 = water2015_s2
    .or(jrcW)
    .unmask(0)
    .rename('water2015');

  // Built-up
  var builtT = builtProxyMask(dwY, s2y);
  var built2015 = builtProxyMask(dw_2015, s2_2015);

  var aoiKm2 = totalAoiKm2(aoi);
  var builtKm2 = maskAreaKm2(builtT, aoi, 10);
  var builtKm2_2015 = maskAreaKm2(built2015, aoi, 10);

  // Reclamation / water-to-land proxy
  var landLike = landLikeMask(dwY, s2y);
  var w2l = waterToLandMask(water2015, landLike);
  var w2lKm2 = maskAreaKm2(w2l, aoi, 10);

  // Shoreline proxy.
  // If current S2 is empty, set shoreline proxy to 0 to avoid false loss from fallback water image.
  var waterT = annualWaterMaskS2(s2y);
  var shoreRaw = shorelineLandGainKm2(waterT, water2015, aoi, 10);
  var s2EmptyNow = safeNumber(s2y.get('s2_empty_flag'), 1);
  var shoreKm2 = ee.Number(
    ee.Algorithms.If(
      s2EmptyNow.eq(1),
      0,
      shoreRaw
    )
  );

  // Yard-like proxy
  var yardM = yardLikeMask(dwY, s2y);
  var yard2015 = yardLikeMask(dw_2015, s2_2015);

  var yardKm2 = maskAreaKm2(yardM, aoi, 10);
  var yardKm2_2015 = maskAreaKm2(yard2015, aoi, 10);

  var yStat = yardPatchStats(yardM, aoi, 10);

  // Quality flags
  var q = sceneQualityFlags(
    s2y.get('s2_scene_count'),
    dwY.get('dw_image_count')
  );

  var wpiFlag = wpiLocationCheckFlags(
    s2_2015,
    aoi,
    water2015_s2,
    built2015
  );

  var partialYear = year.eq(2026);

  return ee.Feature(null, {
    port_id: feature.get('port_id'),
    port_name: feature.get('port_name'),
    country: feature.get('country'),

    calendar_year: year,
    buffer_radius_km: kmBuffer,
    analysis_scale_m: 10,
    observation_window_label: 'WPI-centered circle; not a port boundary',

    // Built-up / impervious
    builtup_area_km2: builtKm2,
    builtup_share: builtKm2.divide(aoiKm2),
    builtup_change_from_2015_km2: builtKm2.subtract(builtKm2_2015),

    // Reclamation / shoreline
    water_to_land_area_km2: w2lKm2,
    reclamation_proxy_flag: ee.Algorithms.If(
      w2lKm2.gt(RECLAMATION_AREA_FLAG_KM2),
      1,
      0
    ),
    shoreline_change_proxy_km2: shoreKm2,

    // Yard-like paved area
    yard_like_area_km2: yardKm2,
    yard_like_share: yardKm2.divide(aoiKm2),
    yard_like_change_from_2015_km2: yardKm2.subtract(yardKm2_2015),
    yard_like_patch_count: yStat.patchCount,
    largest_yard_like_patch_km2: yStat.largestKm2,

    // AOI and image quality
    aoi_area_km2: aoiKm2,
    s2_scene_count: s2y.get('s2_scene_count'),
    dw_image_count: dwY.get('dw_image_count'),
    s2_empty_flag: s2y.get('s2_empty_flag'),
    dw_empty_flag: dwY.get('dw_empty_flag'),
    s2_2015_empty_flag: s2_2015.get('s2_empty_flag'),
    dw_2015_empty_flag: dw_2015.get('dw_empty_flag'),

    image_quality_flag: q.image_quality_flag,
    cloud_quality_flag: q.cloud_quality_flag,
    wpi_location_check_flag: wpiFlag,
    partial_calendar_year_flag: ee.Algorithms.If(partialYear, 1, 0)
  });
}

// =========================
// Build panel
// =========================

var ports = USE_PILOT30_UPLOADED_ASSET
  ? ee.FeatureCollection(PORTS_ASSET_ID)
  : PILOT_FEATURES;

// Build with client-side year and buffer loops.
// This avoids server-side list nesting and type issues.
var panelFc = ee.FeatureCollection([]);

YEARS.forEach(function(year) {
  BUFFER_KM_LIST.forEach(function(bufferKmValue) {
    var fc = ports.map(function(feature) {
      return analyzePortYearBufferKm(feature, year, bufferKmValue);
    });
    panelFc = panelFc.merge(fc);
  });
});

// =========================
// Debug prints
// =========================

print('YEARS', YEARS);
print('BUFFER_KM_LIST', BUFFER_KM_LIST);
print('USE_PILOT30_UPLOADED_ASSET', USE_PILOT30_UPLOADED_ASSET);
print('PORTS_ASSET_ID', PORTS_ASSET_ID);
print('Ports size', ports.size());
print('Ports first feature', ports.first());
print('Panel size expected approx', ports.size().multiply(YEARS.length).multiply(BUFFER_KM_LIST.length));
print('Panel size actual', panelFc.size());
print('Panel first feature', panelFc.first());

Map.addLayer(ports, {color: 'yellow'}, 'ports');
Map.centerObject(ports, 4);

// =========================
// Export
// =========================

Export.table.toDrive({
  collection: panelFc,
  description: 'port_satellite_scale_panel_pilot30',
  folder: 'gee_exports',
  fileFormat: 'CSV'
});