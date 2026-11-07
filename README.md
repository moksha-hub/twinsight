# TwinSight

TwinSight is a small, explainable research prototype for comparing flood-related water-change evidence from Sentinel-2 optical imagery and Sentinel-1 radar. It highlights where both sensors agree, where only one sensor indicates change, and how much area could not be compared reliably.

The app queries public imagery through Microsoft Planetary Computer's STAC API and processes small Cloud Optimized GeoTIFF (COG) windows locally. No paid API key is required. The result is an exploratory analysis aid, not a validated flood map or emergency product.

## Start the app

Requirements: Python 3.10–3.12, internet access, and enough memory for raster reprojection. Windows, macOS, and Linux are supported by the Python dependencies.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

On macOS or Linux, replace the activation command with `source .venv/bin/activate`, then run `python -m pip install -r requirements.txt` and `streamlit run app.py`.

The default study area and date are a real-data smoke-test window for Chennai (31 October 2024). Select **Run evidence review** to query the catalogue and retrieve the corresponding imagery. Raster retrieval can take a minute or two, depending on the network and source response time.

## Use the dashboard

1. Choose a preset region or enter a WGS84 bounding box as west, south, east, north.
2. Set the event date, event search window, baseline lead time, and maximum Sentinel-2 scene cloud percentage.
3. Run the review. TwinSight searches for event and baseline scenes from both sensors.
4. Inspect the agreement map, acquisition dates, cloud cover, sensor timing, and usable-area coverage before interpreting the result.
5. Download evidence polygons as GeoJSON or the complete scene and threshold metadata as JSON.

The app pairs Sentinel-2 and Sentinel-1 acquisitions with the smallest date gap in each requested interval. It reports the event and baseline date gaps because unmatched acquisition timing can make apparent disagreement misleading. If a search returns no scenes, widen the date interval or choose another area. If coverage is low, use a clearer event window or reduce the study area.

## Data and method

### Public data

- Sentinel-2 L2A: `sentinel-2-l2a` collection. Green (B03), short-wave infrared (B11), and scene classification (SCL) assets are used.
- Sentinel-1 RTC: `sentinel-1-rtc` collection. The VV backscatter asset is used.
- Catalogue and signed asset access: Microsoft Planetary Computer STAC API.

### Prototype evidence rules

- All rasters are warped onto a shared 360 × 360 EPSG:4326 grid covering the selected bounding box.
- Optical water evidence uses MNDWI = (B03 − B11) / (B03 + B11), with a fixed water threshold of 0.12. SCL fill, saturated pixels, cloud shadow, cloud, cirrus, and snow classes are masked in both event and baseline observations.
- Radar water-change evidence uses Sentinel-1 RTC VV. A pixel is flagged when event backscatter is below −15.5 dB and the baseline return is more than 2 dB stronger.
- Agreement and sensor-only classes are counted only where optical and radar evidence are both usable. Pixels without comparable coverage remain unclassified; the app reports the optical, radar, and joint coverage separately.
- Mapped area is a coarse pixel-area estimate over the geographic grid. It should not be treated as a precise inundation area.

These fixed thresholds are transparent heuristics, not a trained or calibrated classifier. Flooded vegetation, built areas, terrain, residual clouds, seasonal water, speckle, acquisition geometry, and baseline choice can all affect the result.

## Reproducibility and exports

Each GeoJSON feature includes its evidence class, source scene IDs, and acquisition timestamps. The downloadable `twinsight-analysis.json` records the bounding box, grid, thresholds, pixel counts, coverage percentages, and all four scene IDs/dates. Keep both exports with any notes or figures derived from a run.

## Real-data smoke check

The first public-data processing check used the Chennai Adyar bounding box, event date 31 October 2024, a 12-day event search window, a 24-day baseline lead, and an 80% maximum scene-cloud filter. The catalogue returned Sentinel-2 acquisitions on 31 October and 1 October, and Sentinel-1 acquisitions on 31 October and 7 October. Processing completed. Only about 10.6% of the 360 × 360 area had joint usable coverage; this confirms the retrieval and analysis path, not flood-detection accuracy. The app warns when paired coverage falls below 25%.

## Research validation still needed

Before making scientific claims, compare optical-only, radar-only, and combined evidence against an independent reference flood map. Report precision, recall, intersection-over-union, event-area error, valid-coverage fraction, cloud-related false alarms, and results across several events and land-cover types. Record acquisition gaps and investigate an abstention threshold for low-coverage areas.

## Troubleshooting

- **No matching scenes:** try a wider search window, another date, or another region; the two archives have different coverage and revisit timing.
- **Very low paired coverage:** clouds or raster overlap may remove most pixels. The map displays masked areas; do not interpret a small valid patch as representative of the whole box.
- **Slow run:** the first run downloads remote COG blocks and reprojects multiple bands. Reduce the bounding-box size for faster iteration.
- **Asset or network error:** confirm internet access and rerun. Public service availability and throttling are outside this app's control.

## Project files

- `app.py` — Streamlit interface, STAC discovery, raster processing, evidence map, and exports.
- `requirements.txt` — free, open-source Python dependencies.
- `.gitignore` — local environments, caches, secrets, and generated raster outputs.
