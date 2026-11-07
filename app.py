from __future__ import annotations

import json
import math
from datetime import date, timedelta

import folium
import numpy as np
import pandas as pd
import planetary_computer
import rasterio
import streamlit as st
from folium.raster_layers import ImageOverlay
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.transform import from_bounds
from rasterio.warp import reproject
from streamlit_folium import st_folium


STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
PRESETS = {
    "Chennai · Adyar estuary": (80.20, 12.93, 80.31, 13.04),
    "Kosi · Bihar floodplain": (86.70, 25.30, 86.84, 25.43),
    "Assam · Brahmaputra reach": (91.58, 26.10, 91.73, 26.23),
    "Kerala · Alappuzha coast": (76.28, 9.40, 76.42, 9.53),
    "Custom coordinates": (80.20, 12.93, 80.31, 13.04),
}
COLORS = {0: (232, 228, 214, 0), 1: (15, 116, 130, 205), 2: (216, 157, 53, 220), 3: (198, 91, 65, 220)}

st.set_page_config(page_title="TwinSight · Evidence desk", page_icon="◒", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Source+Sans+3:wght@400;500;600;700&display=swap');
:root { --paper:#f3f1e9; --ink:#202c2b; --muted:#687572; --line:#d8d9ce; --teal:#1d6870; --ochre:#bd852a; }
.stApp { background:var(--paper); color:var(--ink); font-family:'Source Sans 3',sans-serif; }
[data-testid="stSidebar"] { background:#e8e9df; border-right:1px solid var(--line); }
[data-testid="stSidebar"] * { color:var(--ink); }
h1,h2,h3 { font-family:'Barlow Condensed',sans-serif !important; letter-spacing:-.015em; color:var(--ink); }
h1 { font-size:2.45rem !important; font-weight:600 !important; }
.eyebrow { font-size:.7rem; letter-spacing:.16em; text-transform:uppercase; color:var(--teal); font-weight:700; }
.masthead { border-bottom:1px solid var(--line); padding:0 0 1.2rem; margin-bottom:1.2rem; display:flex; justify-content:space-between; align-items:flex-end; }
.brandline { display:flex; align-items:center; gap:14px; }
.brandmark { width:34px;height:34px;border:1px solid var(--teal);border-radius:50%;display:grid;place-items:center;color:var(--teal);font-size:19px; }
.subline { color:var(--muted); font-size:.92rem; margin-top:-.4rem; }
.metric { border-top:2px solid var(--ink); padding:10px 2px 4px; min-height:74px; }
.metric .value { font:600 1.8rem 'Barlow Condensed',sans-serif; color:var(--ink); }
.metric .label { font-size:.68rem; text-transform:uppercase; letter-spacing:.1em; color:var(--muted); }
.section-label { font-size:.7rem; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); font-weight:700; padding:8px 0; border-bottom:1px solid var(--line); margin-bottom:10px; }
.note { background:#e7e8df; padding:12px 14px; border-radius:2px; color:#43514d; font-size:.86rem; line-height:1.5; }
.legend-row { display:flex; gap:9px; align-items:center; font-size:.83rem; color:var(--ink); margin:5px 0; }
.swatch { width:13px;height:13px;display:inline-block;border-radius:50%; }
div[data-testid="stMetric"] { background:transparent; border:0; padding:0; }
.stButton>button { border-radius:2px; border:1px solid var(--teal); color:var(--teal); background:transparent; font-weight:600; }
.stButton>button[kind="primary"] { color:#f5f2e9; background:var(--teal); }
.stDownloadButton>button { border-radius:2px; }
footer { visibility:hidden; }
</style>
""", unsafe_allow_html=True)


def find_items(collection: str, bbox: tuple[float, float, float, float], start: date, end: date, limit: int = 12):
    client = Client.open(STAC_URL)
    q = client.search(collections=[collection], bbox=bbox, datetime=f"{start.isoformat()}/{end.isoformat()}", max_items=limit)
    items = list(q.items())
    items.sort(key=lambda x: x.datetime or start, reverse=True)
    return items


def asset_key(item, *keys):
    for key in keys:
        if key in item.assets:
            return item.assets[key]
    return None


def match_scenes(optical_items, radar_items):
    """Choose an optical/radar pair with the smallest acquisition-date gap."""
    if not optical_items or not radar_items:
        raise ValueError("No optical/radar scene pair is available in this time window.")
    candidates = []
    for optical in optical_items:
        for radar in radar_items:
            gap = abs((optical.datetime.date() - radar.datetime.date()).days)
            cloud = optical.properties.get("eo:cloud_cover", 100)
            newest = max(optical.datetime.timestamp(), radar.datetime.timestamp())
            candidates.append(((gap, cloud, -newest), optical, radar))
    _, optical, radar = min(candidates, key=lambda candidate: candidate[0])
    return optical, radar


def read_roi(asset, bbox, shape=(360, 360), categorical=False):
    """Warp a small COG window onto one shared WGS84 grid for aligned comparison."""
    href = asset.href
    with rasterio.open(href) as ds:
        arr = np.full(shape, np.nan, dtype="float32")
        reproject(source=rasterio.band(ds, 1), destination=arr,
                  src_transform=ds.transform, src_crs=ds.crs, src_nodata=ds.nodata,
                  dst_transform=from_bounds(*bbox, shape[1], shape[0]), dst_crs="EPSG:4326",
                  dst_nodata=np.nan,
                  resampling=Resampling.nearest if categorical else Resampling.bilinear,
                  num_threads=2)
        if not np.isfinite(arr).any():
            raise ValueError("The selected scene does not cover the requested area.")
        return arr


def signed(item):
    return planetary_computer.sign(item)


def get_sensor_arrays(s2, s2pre, s1, s1pre, bbox):
    s2, s2pre, s1, s1pre = map(signed, [s2, s2pre, s1, s1pre])
    green = asset_key(s2, "B03", "green"); swir = asset_key(s2, "B11", "swir16")
    green0 = asset_key(s2pre, "B03", "green"); swir0 = asset_key(s2pre, "B11", "swir16")
    vv = asset_key(s1, "vv"); vv0 = asset_key(s1pre, "vv")
    scl = asset_key(s2, "SCL", "scl")
    scl0 = asset_key(s2pre, "SCL", "scl")
    if not all([green, swir, green0, swir0, vv, vv0]):
        raise ValueError("One or more required bands are missing from the selected scenes.")
    shape = (360, 360)
    g = read_roi(green, bbox, shape); sw = read_roi(swir, bbox, shape)
    g0 = read_roi(green0, bbox, shape); sw0 = read_roi(swir0, bbox, shape)
    radar = read_roi(vv, bbox, shape); radar0 = read_roi(vv0, bbox, shape)
    cloud = read_roi(scl, bbox, shape, True) if scl else np.zeros(shape, dtype="float32")
    cloud0 = read_roi(scl0, bbox, shape, True) if scl0 else np.zeros(shape, dtype="float32")
    # Sentinel-2 L2A reflectance values are scaled integers in the STAC COGs.
    optical = (g - sw) / np.maximum(g + sw, 1e-6)
    optical0 = (g0 - sw0) / np.maximum(g0 + sw0, 1e-6)
    clear = (~np.isin(cloud, [0, 1, 3, 8, 9, 10, 11]) &
             ~np.isin(cloud0, [0, 1, 3, 8, 9, 10, 11]) &
             np.isfinite(optical) & np.isfinite(optical0))
    water = (optical > 0.12) & clear
    water0 = (optical0 > 0.12) & clear
    optical_change = water & ~water0
    # RTC VV is linear power; compare relative change in dB and require a low event return.
    db = 10 * np.log10(np.maximum(radar, 1e-8)); db0 = 10 * np.log10(np.maximum(radar0, 1e-8))
    radar_valid = np.isfinite(db) & np.isfinite(db0)
    radar_change = (db < -15.5) & ((db0 - db) > 2.0) & radar_valid
    classes = np.zeros(shape, dtype="uint8")
    classes[optical_change & radar_change] = 1
    classes[optical_change & ~radar_change] = 2
    classes[~optical_change & radar_change] = 3
    valid = clear & radar_valid
    classes[~valid] = 255
    return classes, clear, radar_valid, optical, db


def map_image(classes):
    rgba = np.zeros((*classes.shape, 4), dtype=np.uint8)
    for k, color in COLORS.items():
        rgba[classes == k] = color
    rgba[classes == 255] = (90, 98, 90, 90)
    return rgba


def make_geojson(classes, bbox, s2, s1):
    h, w = classes.shape
    transform = from_bounds(*bbox, w, h)
    features = []
    for geom, value in shapes(classes, mask=np.isin(classes, [1, 2, 3]), transform=transform):
        v = int(value)
        features.append({"type": "Feature", "geometry": geom, "properties": {
            "class": {1: "sensor_agreement", 2: "optical_only", 3: "radar_only"}[v],
            "optical_scene": s2.id, "radar_scene": s1.id,
            "optical_date": s2.datetime.isoformat(), "radar_date": s1.datetime.isoformat(),
        }})
    return {"type": "FeatureCollection", "name": "TwinSight evidence classes", "features": features}


def make_report(result, counts, coverage):
    s2, s1 = result["s2"], result["s1"]
    s2pre, s1pre = result["s2pre"], result["s1pre"]
    detected_pixels = sum(counts.values())
    return {
        "product": "TwinSight",
        "analysis_type": "optical_sar_water_change_disagreement",
        "data_source": "Microsoft Planetary Computer STAC",
        "collections": ["sentinel-2-l2a", "sentinel-1-rtc"],
        "area_bbox_wgs84": list(result["bbox"]),
        "grid": {"crs": "EPSG:4326", "width": int(result["classes"].shape[1]), "height": int(result["classes"].shape[0])},
        "thresholds": {"mndwi_water": 0.12, "vv_event_db_below": -15.5, "vv_baseline_drop_db_above": 2.0},
        "coverage_percent": coverage,
        "class_pixel_counts": {"agreement": counts[1], "optical_only": counts[2], "radar_only": counts[3]},
        "agreement_percent_of_detected_pixels": round(counts[1] / max(detected_pixels, 1) * 100, 2),
        "disagreement_percent_of_detected_pixels": round((counts[2] + counts[3]) / max(detected_pixels, 1) * 100, 2),
        "mapped_change_area_km2_approx": round(area_km2(detected_pixels, result["bbox"], result["classes"].shape[1]), 3),
        "scenes": {
            "optical_event": {"id": s2.id, "datetime": s2.datetime.isoformat(), "cloud_percent": s2.properties.get("eo:cloud_cover")},
            "radar_event": {"id": s1.id, "datetime": s1.datetime.isoformat()},
            "optical_baseline": {"id": s2pre.id, "datetime": s2pre.datetime.isoformat(), "cloud_percent": s2pre.properties.get("eo:cloud_cover")},
            "radar_baseline": {"id": s1pre.id, "datetime": s1pre.datetime.isoformat()},
        },
        "caveat": "Prototype thresholds; not a validated flood classifier or emergency product.",
    }


def area_km2(pixel_count, bbox, width):
    lonmid = (bbox[0] + bbox[2]) / 2
    latmid = (bbox[1] + bbox[3]) / 2
    width_m = (bbox[2] - bbox[0]) * 111320 * math.cos(math.radians(latmid))
    height_m = (bbox[3] - bbox[1]) * 110540
    return pixel_count * (width_m / width) * (height_m / 360) / 1_000_000


st.markdown("""
<div class="masthead"><div><div class="brandline"><div class="brandmark">◒</div><div><div class="eyebrow">TwinSight / Evidence desk</div><h1 style="margin:.08rem 0 0">Flood event verification</h1></div></div><div class="subline">Optical + SAR · explainable disagreement analysis</div></div><div class="eyebrow">Research prototype&nbsp;&nbsp; / &nbsp;&nbsp; v0.2</div></div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown('<div class="eyebrow">01 / Study area</div>', unsafe_allow_html=True)
    preset = st.selectbox("Region", list(PRESETS))
    bbox = PRESETS[preset]
    if preset == "Custom coordinates":
        c1, c2 = st.columns(2)
        west = c1.number_input("West", value=bbox[0], format="%.4f")
        south = c2.number_input("South", value=bbox[1], format="%.4f")
        east = c1.number_input("East", value=bbox[2], format="%.4f")
        north = c2.number_input("North", value=bbox[3], format="%.4f")
        bbox = (west, south, east, north)
    st.markdown('<div class="eyebrow" style="margin-top:1.4rem">02 / Event window</div>', unsafe_allow_html=True)
    event_day = st.date_input("Event date", value=date(2024, 10, 31), max_value=date.today())
    days = st.slider("Event search window · days", 5, 30, 12)
    baseline_days = st.slider("Baseline lead time · days", 10, 90, 24)
    cloud_limit = st.slider("Optical cloud cover · max %", 0, 80, 80)
    run = st.button("Run evidence review", type="primary", use_container_width=True)
    st.markdown('<div class="note" style="margin-top:1rem">TwinSight measures evidence agreement. It does not confirm flood extent without independent ground truth.</div>', unsafe_allow_html=True)

if "result" not in st.session_state:
    st.session_state.result = None
if run:
    st.session_state.result = None
    with st.spinner("Searching open satellite catalogues and reading selected raster windows…"):
        try:
            window_start = event_day - timedelta(days=days)
            base_end = event_day - timedelta(days=baseline_days)
            base_start = base_end - timedelta(days=days)
            s2_items = find_items("sentinel-2-l2a", bbox, window_start, event_day, 12)
            s2_items = [i for i in s2_items if i.properties.get("eo:cloud_cover", 100) <= cloud_limit]
            s2_pre_items = find_items("sentinel-2-l2a", bbox, base_start, base_end, 12)
            s2_pre_items = [i for i in s2_pre_items if i.properties.get("eo:cloud_cover", 100) <= cloud_limit]
            s1_items = find_items("sentinel-1-rtc", bbox, window_start, event_day, 12)
            s1_pre_items = find_items("sentinel-1-rtc", bbox, base_start, base_end, 12)
            if not all([s2_items, s2_pre_items, s1_items, s1_pre_items]):
                raise ValueError("A matching pre-event or event scene was not found for both sensors. Widen the search window or choose another date/area.")
            s2, s1 = match_scenes(s2_items, s1_items)
            s2pre, s1pre = match_scenes(s2_pre_items, s1_pre_items)
            classes, clear, radar_valid, optical, db = get_sensor_arrays(s2, s2pre, s1, s1pre, bbox)
            st.session_state.result = {"classes": classes, "bbox": bbox, "s2": s2,
                                       "s1": s1, "s2pre": s2pre, "s1pre": s1pre,
                                       "clear": clear, "radar_valid": radar_valid}
        except Exception as exc:
            st.error(f"Data review could not complete: {exc}")

result = st.session_state.result
if result is None:
    st.markdown('<div class="note">Choose an area and event window, then run a review to query public Sentinel scenes. Results appear only after real imagery has been retrieved and processed.</div>', unsafe_allow_html=True)
    st.stop()

classes = result["classes"]
bbox = result["bbox"]
valid_mask = np.isin(classes, [0, 1, 2, 3])
counts = {k: int(np.count_nonzero(classes == k)) for k in [1, 2, 3]}
active_pixels = sum(counts.values())
agree_pct = (counts[1] / max(active_pixels, 1)) * 100
disagree_pct = ((counts[2] + counts[3]) / max(active_pixels, 1)) * 100
coverage_pct = np.count_nonzero(valid_mask) / classes.size * 100
optical_coverage_pct = np.count_nonzero(result["clear"]) / classes.size * 100
radar_coverage_pct = np.count_nonzero(result["radar_valid"]) / classes.size * 100
flood_area = area_km2(active_pixels, bbox, classes.shape[1])

st.success("Satellite scene analysis complete · public Planetary Computer STAC assets")
if coverage_pct < 25:
    st.warning(f"Only {coverage_pct:.1f}% of the selected area has comparable optical and radar coverage. Interpret mapped changes cautiously.")

metric_cols = st.columns(4)
for col, value, label in zip(metric_cols,
        [f"{flood_area:.1f} km²", f"{agree_pct:.0f}%", f"{disagree_pct:.0f}%", f"{coverage_pct:.0f}%"],
        ["Mapped change · approx.", "Sensor agreement", "Sensor disagreement", "Comparable coverage"]):
    with col:
        st.markdown(f'<div class="metric"><div class="value">{value}</div><div class="label">{label}</div></div>', unsafe_allow_html=True)

left, right = st.columns([1.75, 1], gap="large")
with left:
    st.markdown('<div class="section-label">Evidence map / optical × radar change</div>', unsafe_allow_html=True)
    center = [(bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2]
    fmap = folium.Map(location=center, zoom_start=11, tiles="CartoDB positron", control_scale=True)
    overlay = ImageOverlay(image=map_image(classes), bounds=[[bbox[1], bbox[0]], [bbox[3], bbox[2]]],
                          opacity=0.9, interactive=True, cross_origin=False, zindex=2, name="Evidence classes")
    overlay.add_to(fmap)
    folium.LayerControl(collapsed=True).add_to(fmap)
    st_folium(fmap, height=480, use_container_width=True, returned_objects=[])
    st.markdown('<div class="legend-row"><span class="swatch" style="background:#0f7482"></span> Both sensors indicate new water</div><div class="legend-row"><span class="swatch" style="background:#d89d35"></span> Optical-only change</div><div class="legend-row"><span class="swatch" style="background:#c65b41"></span> Radar-only change</div><div class="legend-row"><span class="swatch" style="background:#737c73"></span> Insufficient comparable evidence</div>', unsafe_allow_html=True)
with right:
    st.markdown('<div class="section-label">Reading the result</div>', unsafe_allow_html=True)
    st.markdown(f"**{counts[1]:,}** pixels show agreement, while **{counts[2] + counts[3]:,}** show sensor-specific change. Mapped area is a coarse estimate over the selected bounding box.")
    s2, s1 = result["s2"], result["s1"]
    temporal_gap = abs((s2.datetime.date() - s1.datetime.date()).days)
    st.markdown(f"**Optical scene** · Sentinel-2 L2A  {s2.datetime:%d %b %Y} · {s2.properties.get('eo:cloud_cover', '—'):.1f}% cloud · `{s2.id}`")
    st.markdown(f"**Radar scene** · Sentinel-1 RTC  {s1.datetime:%d %b %Y} · {temporal_gap} day gap · `{s1.id}`")
    if temporal_gap > 6:
        st.warning(f"Acquisitions are {temporal_gap} days apart. Treat disagreement as temporally confounded.")
    baseline_gap = abs((result["s2pre"].datetime.date() - result["s1pre"].datetime.date()).days)
    st.markdown(f"**Usable coverage** · optical {optical_coverage_pct:.1f}% · radar {radar_coverage_pct:.1f}% · jointly comparable {coverage_pct:.1f}%")
    st.caption(f"Baseline pair: {result['s2pre'].datetime:%d %b %Y} optical / {result['s1pre'].datetime:%d %b %Y} radar · {baseline_gap} day gap")
    if baseline_gap > 6:
        st.warning(f"Baseline acquisitions are {baseline_gap} days apart. Baseline comparison may be temporally confounded.")
    st.markdown('<div class="section-label" style="margin-top:1.2rem">Method notes</div>', unsafe_allow_html=True)
    st.caption("Optical evidence: MNDWI threshold against a pre-event baseline with Sentinel-2 scene classification masking. Radar evidence: low VV return plus relative decrease from pre-event Sentinel-1 RTC. The simple thresholds are inspectable prototype heuristics, not a validated flood classifier.")

st.markdown('<div class="section-label" style="margin-top:1.5rem">Scene ledger</div>', unsafe_allow_html=True)
ledger = pd.DataFrame([
        {"Sensor": "Sentinel-2 · optical", "Role": "Event", "Acquisition": result["s2"].datetime.strftime("%Y-%m-%d"), "Processing": f"L2A · {result['s2'].properties.get('eo:cloud_cover', '—'):.1f}% cloud", "Source": result["s2"].id},
        {"Sensor": "Sentinel-2 · optical", "Role": "Baseline", "Acquisition": result["s2pre"].datetime.strftime("%Y-%m-%d"), "Processing": f"L2A · {result['s2pre'].properties.get('eo:cloud_cover', '—'):.1f}% cloud", "Source": result["s2pre"].id},
        {"Sensor": "Sentinel-1 · SAR", "Role": "Event", "Acquisition": result["s1"].datetime.strftime("%Y-%m-%d"), "Processing": "RTC · VV", "Source": result["s1"].id},
        {"Sensor": "Sentinel-1 · SAR", "Role": "Baseline", "Acquisition": result["s1pre"].datetime.strftime("%Y-%m-%d"), "Processing": "RTC · VV", "Source": result["s1pre"].id},
])
st.dataframe(ledger, use_container_width=True, hide_index=True)

geojson = make_geojson(classes, bbox, result["s2"], result["s1"])
st.download_button("Download evidence polygons · GeoJSON", json.dumps(geojson),
                   file_name="twinsight-evidence.geojson", mime="application/geo+json")
report = make_report(result, counts, {"optical": optical_coverage_pct, "radar": radar_coverage_pct, "joint": coverage_pct})
st.download_button("Download analysis metadata · JSON", json.dumps(report, indent=2),
                   file_name="twinsight-analysis.json", mime="application/json")

st.markdown('<div class="note" style="margin-top:1.3rem">Interpretation limit: optical and radar observations may differ in acquisition time, viewing geometry, land cover response and processing history. This prototype does not apply terrain correction to raw GRD, use calibrated ground truth, or provide emergency guidance.</div>', unsafe_allow_html=True)
