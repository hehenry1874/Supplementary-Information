/**
 * Port physical expansion proxy — LOW-CONFIDENCE supplementary rerun (extended S2/DW window + 20 km cap).
 *
 * Upload `gee/ports_wpi_low_confidence_upload.csv` (longitude/latitude) or GeoJSON as PORTS_ASSET_ID.
 * Run one export per batch (BATCH_START / EXPORT_SUFFIX); download CSVs to `gee/exports_lowconf_supp/`.
 *
 * Indicators are PROXIES in a standardized observation window (not port boundaries):
 * - Near-port built-up / impervious proxy
 * - Water-to-land / reclamation proxy
 * - Yard-like paved open area proxy
 *
 * Observation windows are standardized WPI-centered buffers.
 * They are NOT official port boundaries or true berth footprints.
 *
 * Key features:
 * - Defensive buffer handling; shared 10 km composite per port-year for 5/10 km zonal stats
 * - Empty S2 / DW fallbacks; safe reduceRegion; 2015 & 2018 baselines
 * - Batch export for full panel; optional partial 2026 (satellite_year_type)
 */

// =========================
// CONFIG
// =========================

// 2026 = partial calendar year (same semantics as full-safe script).
var INCLUDE_2026_PARTIAL = true;

var YEARS = [2015, 2018, 2021, 2024, 2026];

var BUFFER_KM_LIST = [5, 10, 20];
var MAX_BUFFER_KM = 20;

var USE_UPLOADED_ASSET = true;
// TODO: set to your uploaded low-confidence ports Table asset.
var PORTS_ASSET_ID = 'projects/my-project-1/assets/ports_wpi_low_confidence';

// Batch size 30; last batch may be shorter. Example: BATCH_START=0, EXPORT_SUFFIX=lowconf_batch000_030
var BATCH_START = 0;
var BATCH_SIZE = 30;
var EXPORT_SUFFIX = 'lowconf_batch000_030';

var EXPORT_YEAR_TAG = INCLUDE_2026_PARTIAL ? 'with2026' : 'no2026';

// Wider cloud prescreen + extended compositing window (see s2Collection / dwBuiltCollection).
var MAX_CLOUD_PRESCREEN_PCT = 80;
var TEMPORAL_PAD_MONTHS = 2;
var TEMPORAL_WINDOW_TYPE = 'calendar_year_pm2months';
var SUPPLEMENT_MODE = 'lowconf_extended';
var MIN_SCENES_GOOD = 8;
var MIN_DW_IMAGES_GOOD = 4;

var DW_BUILT_PROB_THRESH = 0.35;
var NDVI_LOW_MAX = 0.12;
var NDBI_BUILT_MIN = 0.05;

var MNDWI_WATER_MIN = -0.1;
var MNDWI_WATER_STRICT = 0.05;
var JRC_OCC_WATER_MIN = 80;

var RECLAMATION_AREA_FLAG_KM2 = 0.01;

var PATCH_METRICS_ENABLED = false;

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

function safeStringProp(feature, primary, fallback, defaultValue) {
  var names = feature.propertyNames();

  var primaryValue = ee.Algorithms.If(
    names.contains(primary),
    feature.get(primary),
    null
  );

  var fallbackValue = ee.Algorithms.If(
    names.contains(fallback),
    feature.get(fallback),
    defaultValue
  );

  return ee.Algorithms.If(
    ee.Algorithms.IsEqual(primaryValue, null),
    fallbackValue,
    primaryValue
  );
}

function positivePart(num) {
  num = ee.Number(num);
  return ee.Number(ee.Algorithms.If(num.lt(0), 0, num));
}

function changeAfterBaseline(currentValue, baselineValue, year, baselineYear) {
  var diff = ee.Number(currentValue).subtract(ee.Number(baselineValue));
  return ee.Number(
    ee.Algorithms.If(
      ee.Number(year).gte(baselineYear),
      diff,
      -999
    )
  );
}

function positiveChangeAfterBaseline(currentValue, baselineValue, year, baselineYear) {
  var signed = changeAfterBaseline(currentValue, baselineValue, year, baselineYear);
  return ee.Number(
    ee.Algorithms.If(
      ee.Number(year).gte(baselineYear),
      positivePart(signed),
      -999
    )
  );
}

function bufferKm(geom, km) {
  var distanceMeters = ee.Number(km).multiply(1000);
  return geom.buffer(distanceMeters, 30);
}

function totalAoiKm2(aoi) {
  return ee.Number(aoi.area(1)).divide(1e6);
}

function s2Collection(year, aoi) {
  var y = ee.Number(year);
  var start = ee.Date.fromYMD(y, 1, 1).advance(-TEMPORAL_PAD_MONTHS, 'month');
  var end = ee.Date.fromYMD(y, 1, 1)
    .advance(1, 'year')
    .advance(TEMPORAL_PAD_MONTHS, 'month');

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

function dwBuiltCollection(year, aoi) {
  var y = ee.Number(year);
  var start = ee.Date.fromYMD(y, 1, 1).advance(-TEMPORAL_PAD_MONTHS, 'month');
  var end = ee.Date.fromYMD(y, 1, 1)
    .advance(1, 'year')
    .advance(TEMPORAL_PAD_MONTHS, 'month');

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

function jrcWaterReference(aoi) {
  var occ = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence');

  return occ.gte(JRC_OCC_WATER_MIN)
    .unmask(0)
    .clip(aoi)
    .rename('jrc_water');
}

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

function waterToLandMask(waterBaseline, landLikeT) {
  return waterBaseline.unmask(0)
    .and(landLikeT.unmask(0))
    .rename('w2l');
}

function shorelineLossFromBaseline(waterT, waterBaselineS2) {
  return waterBaselineS2.unmask(0)
    .and(waterT.unmask(0).not())
    .rename('shoreline_loss');
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

function yardPatchStats(yardMask, aoi, scaleM) {
  return {
    patchCount: ee.Number(-999),
    largestKm2: ee.Number(-999)
  };
}

function makePortYearImages(feature, year) {
  var geom = feature.geometry();
  var aoiMax = bufferKm(geom, MAX_BUFFER_KM);

  var s2y = annualMedianS2(year, aoiMax);
  var dwY = annualMedianBuiltProb(year, aoiMax);

  var s2_2015 = annualMedianS2(2015, aoiMax);
  var dw_2015 = annualMedianBuiltProb(2015, aoiMax);

  var s2_2018 = annualMedianS2(2018, aoiMax);
  var dw_2018 = annualMedianBuiltProb(2018, aoiMax);

  var water2015_s2 = annualWaterMaskS2(s2_2015);
  var water2018_s2 = annualWaterMaskS2(s2_2018);

  var jrcW = jrcWaterReference(aoiMax);

  var water2015_jrc = water2015_s2
    .or(jrcW)
    .unmask(0)
    .rename('water2015_jrc');

  var water2018_jrc = water2018_s2
    .or(jrcW)
    .unmask(0)
    .rename('water2018_jrc');

  var builtT = builtProxyMask(dwY, s2y);
  var built2015 = builtProxyMask(dw_2015, s2_2015);
  var built2018 = builtProxyMask(dw_2018, s2_2018);

  var landLikeT = landLikeMask(dwY, s2y);
  var landLike2015 = landLikeMask(dw_2015, s2_2015);
  var landLike2018 = landLikeMask(dw_2018, s2_2018);

  var waterT = annualWaterMaskS2(s2y);

  var yardT = yardLikeMask(dwY, s2y);
  var yard2015 = yardLikeMask(dw_2015, s2_2015);
  var yard2018 = yardLikeMask(dw_2018, s2_2018);

  return {
    aoiMax: aoiMax,
    s2y: s2y,
    dwY: dwY,
    s2_2015: s2_2015,
    dw_2015: dw_2015,
    s2_2018: s2_2018,
    dw_2018: dw_2018,
    water2015_s2: water2015_s2,
    water2018_s2: water2018_s2,
    water2015_jrc: water2015_jrc,
    water2018_jrc: water2018_jrc,
    builtT: builtT,
    built2015: built2015,
    built2018: built2018,
    landLikeT: landLikeT,
    landLike2015: landLike2015,
    landLike2018: landLike2018,
    waterT: waterT,
    yardT: yardT,
    yard2015: yard2015,
    yard2018: yard2018
  };
}

function makeFeatureForBuffer(feature, year, kmBuffer, imgs) {
  feature = ee.Feature(feature);
  year = ee.Number(year);
  kmBuffer = ee.Number(kmBuffer);

  var geom = feature.geometry();
  var aoi = bufferKm(geom, kmBuffer);
  var aoiKm2 = totalAoiKm2(aoi);

  var portName = safeStringProp(feature, 'port_name', 'port_name_standard', '');
  var portId = safeStringProp(feature, 'port_id', 'id', '');
  var country = safeStringProp(feature, 'country', 'iso_country', '');

  var builtKm2 = maskAreaKm2(imgs.builtT, aoi, 10);
  var builtKm2_2015 = maskAreaKm2(imgs.built2015, aoi, 10);
  var builtKm2_2018 = maskAreaKm2(imgs.built2018, aoi, 10);

  var builtChg15 = builtKm2.subtract(builtKm2_2015);
  var builtChg15Pos = positivePart(builtChg15);

  var builtChg18 = changeAfterBaseline(builtKm2, builtKm2_2018, year, 2018);
  var builtChg18Pos = positiveChangeAfterBaseline(builtKm2, builtKm2_2018, year, 2018);

  var w2lT15 = waterToLandMask(imgs.water2015_jrc, imgs.landLikeT);
  var w2l2015 = waterToLandMask(imgs.water2015_jrc, imgs.landLike2015);
  var w2lKm2 = maskAreaKm2(w2lT15, aoi, 10);
  var w2lKm2_2015 = maskAreaKm2(w2l2015, aoi, 10);

  var w2lChg15 = w2lKm2.subtract(w2lKm2_2015);
  var w2lChg15Pos = positivePart(w2lChg15);

  var w2lT18 = waterToLandMask(imgs.water2018_jrc, imgs.landLikeT);
  var w2l2018 = waterToLandMask(imgs.water2018_jrc, imgs.landLike2018);
  var w2lKm2_18line = maskAreaKm2(w2lT18, aoi, 10);
  var w2lKm2_2018 = maskAreaKm2(w2l2018, aoi, 10);

  var w2lChg18 = changeAfterBaseline(w2lKm2_18line, w2lKm2_2018, year, 2018);
  var w2lChg18Pos = positiveChangeAfterBaseline(w2lKm2_18line, w2lKm2_2018, year, 2018);

  var shore15Mask = shorelineLossFromBaseline(imgs.waterT, imgs.water2015_s2);
  var shore18Mask = shorelineLossFromBaseline(imgs.waterT, imgs.water2018_s2);

  var shore15Raw = maskAreaKm2(shore15Mask, aoi, 10);
  var shore18Raw = maskAreaKm2(shore18Mask, aoi, 10);

  var s2EmptyNow = safeNumber(imgs.s2y.get('s2_empty_flag'), 1);
  var s2Empty2015 = safeNumber(imgs.s2_2015.get('s2_empty_flag'), 1);
  var s2Empty2018 = safeNumber(imgs.s2_2018.get('s2_empty_flag'), 1);

  var shoreChg15 = ee.Number(
    ee.Algorithms.If(
      s2EmptyNow.eq(1).or(s2Empty2015.eq(1)),
      0,
      shore15Raw
    )
  );

  var shoreChg18 = ee.Number(
    ee.Algorithms.If(
      ee.Number(year).gte(2018),
      ee.Algorithms.If(
        s2EmptyNow.eq(1).or(s2Empty2018.eq(1)),
        0,
        shore18Raw
      ),
      -999
    )
  );

  var yardKm2 = maskAreaKm2(imgs.yardT, aoi, 10);
  var yardKm2_2015 = maskAreaKm2(imgs.yard2015, aoi, 10);
  var yardKm2_2018 = maskAreaKm2(imgs.yard2018, aoi, 10);

  var yardChg15 = yardKm2.subtract(yardKm2_2015);
  var yardChg15Pos = positivePart(yardChg15);

  var yardChg18 = changeAfterBaseline(yardKm2, yardKm2_2018, year, 2018);
  var yardChg18Pos = positiveChangeAfterBaseline(yardKm2, yardKm2_2018, year, 2018);

  var yStat = yardPatchStats(imgs.yardT, aoi, 10);

  var q = sceneQualityFlags(
    imgs.s2y.get('s2_scene_count'),
    imgs.dwY.get('dw_image_count')
  );

  var wpiFlag = wpiLocationCheckFlags(
    imgs.s2_2015,
    aoi,
    imgs.water2015_s2,
    imgs.built2015
  );

  var partialYear = year.eq(2026);

  var valid2015BaselineFlag = ee.Algorithms.If(
    s2Empty2015.eq(0).or(safeNumber(imgs.dw_2015.get('dw_empty_flag'), 1).eq(0)),
    1,
    0
  );

  var valid2018BaselineFlag = ee.Algorithms.If(
    s2Empty2018.eq(0).or(safeNumber(imgs.dw_2018.get('dw_empty_flag'), 1).eq(0)),
    1,
    0
  );

  return ee.Feature(null, {
    port_id: portId,
    port_name: portName,
    country: country,

    calendar_year: year,
    buffer_radius_km: kmBuffer,
    analysis_scale_m: 10,
    observation_window_label: 'WPI-centered circle; not a port boundary',

    aoi_area_km2: aoiKm2,

    builtup_area_km2: builtKm2,
    builtup_share: builtKm2.divide(aoiKm2),

    builtup_baseline_2015_km2: builtKm2_2015,
    builtup_change_from_2015_km2: builtChg15,
    builtup_positive_change_from_2015_km2: builtChg15Pos,

    builtup_baseline_2018_km2: builtKm2_2018,
    builtup_change_from_2018_km2: builtChg18,
    builtup_positive_change_from_2018_km2: builtChg18Pos,

    water_to_land_area_km2: w2lKm2,
    water_to_land_baseline_2015_km2: w2lKm2_2015,
    water_to_land_change_from_2015_km2: w2lChg15,
    water_to_land_positive_change_from_2015_km2: w2lChg15Pos,

    water_to_land_area_2018line_km2: w2lKm2_18line,
    water_to_land_baseline_2018_km2: w2lKm2_2018,
    water_to_land_change_from_2018_km2: w2lChg18,
    water_to_land_positive_change_from_2018_km2: w2lChg18Pos,

    reclamation_proxy_flag_2015line: ee.Algorithms.If(
      w2lChg15Pos.gt(RECLAMATION_AREA_FLAG_KM2),
      1,
      0
    ),
    reclamation_proxy_flag_2018line: ee.Algorithms.If(
      w2lChg18Pos.gt(RECLAMATION_AREA_FLAG_KM2),
      1,
      0
    ),

    shoreline_change_from_2015_km2: shoreChg15,
    shoreline_change_from_2018_km2: shoreChg18,

    yard_like_area_km2: yardKm2,
    yard_like_share: yardKm2.divide(aoiKm2),

    yard_like_baseline_2015_km2: yardKm2_2015,
    yard_like_change_from_2015_km2: yardChg15,
    yard_like_positive_change_from_2015_km2: yardChg15Pos,

    yard_like_baseline_2018_km2: yardKm2_2018,
    yard_like_change_from_2018_km2: yardChg18,
    yard_like_positive_change_from_2018_km2: yardChg18Pos,

    yard_like_patch_count: yStat.patchCount,
    largest_yard_like_patch_km2: yStat.largestKm2,

    s2_scene_count: imgs.s2y.get('s2_scene_count'),
    dw_image_count: imgs.dwY.get('dw_image_count'),
    s2_empty_flag: imgs.s2y.get('s2_empty_flag'),
    dw_empty_flag: imgs.dwY.get('dw_empty_flag'),

    s2_2015_empty_flag: imgs.s2_2015.get('s2_empty_flag'),
    dw_2015_empty_flag: imgs.dw_2015.get('dw_empty_flag'),
    s2_2018_empty_flag: imgs.s2_2018.get('s2_empty_flag'),
    dw_2018_empty_flag: imgs.dw_2018.get('dw_empty_flag'),

    valid_2015_baseline_flag: valid2015BaselineFlag,
    valid_2018_baseline_flag: valid2018BaselineFlag,

    image_quality_flag: q.image_quality_flag,
    cloud_quality_flag: q.cloud_quality_flag,
    wpi_location_check_flag: wpiFlag,
    partial_calendar_year_flag: ee.Algorithms.If(partialYear, 1, 0),
    satellite_year_type: ee.Algorithms.If(partialYear, 'partial_year', 'full_year'),

    supplement_mode: SUPPLEMENT_MODE,
    temporal_window_type: TEMPORAL_WINDOW_TYPE
  });
}

function analyzePortYearAllBuffers(feature, year) {
  feature = ee.Feature(feature);
  year = ee.Number(year);

  var imgs = makePortYearImages(feature, year);

  return ee.List(BUFFER_KM_LIST).map(function(kmBuffer) {
    return makeFeatureForBuffer(feature, year, kmBuffer, imgs);
  });
}

var portsAll = USE_UPLOADED_ASSET
  ? ee.FeatureCollection(PORTS_ASSET_ID)
  : PILOT_FEATURES;

var portsList = portsAll.toList(portsAll.size());
var portsBatchList = portsList.slice(BATCH_START, ee.Number(BATCH_START).add(BATCH_SIZE));
var ports = ee.FeatureCollection(portsBatchList);

var panelFc = ee.FeatureCollection([]);

YEARS.forEach(function(yearValue) {
  var perYearNested = ports.toList(ports.size()).map(function(portObj) {
    return analyzePortYearAllBuffers(ee.Feature(portObj), yearValue);
  }).flatten();

  panelFc = panelFc.merge(ee.FeatureCollection(perYearNested));
});

print('YEARS', YEARS);
print('BUFFER_KM_LIST', BUFFER_KM_LIST);
print('INCLUDE_2026_PARTIAL', INCLUDE_2026_PARTIAL);
print('EXPORT_YEAR_TAG', EXPORT_YEAR_TAG);

print('PORTS_ASSET_ID', PORTS_ASSET_ID);
print('Total ports size', portsAll.size());
print('Batch start', BATCH_START);
print('Batch size', BATCH_SIZE);
print('Batch ports size', ports.size());

print(
  'Expected rows in this batch',
  ports.size().multiply(YEARS.length).multiply(BUFFER_KM_LIST.length)
);
print('Panel rows actual', panelFc.size());
print('Panel first feature', panelFc.first());

Map.addLayer(ports, {color: 'yellow'}, 'ports batch');
Map.centerObject(ports, 3);

Export.table.toDrive({
  collection: panelFc,
  description: 'port_satellite_scale_panel_' + EXPORT_SUFFIX + '_supp',
  folder: 'gee_exports',
  fileFormat: 'CSV'
});
