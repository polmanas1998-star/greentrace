# Map backgrounds (optional)

The home page map draws the real 30 m cells (EU JRC GFC2020 forest, Hansen loss) on a dark ground.
If `<plot>.jpg` exists in this folder, `GET /api/plot-map` names it and the page shows it under the
cells. Nothing is invented: without the file, the ground stays dark.

Each image must cover exactly its frame, north up, EPSG:4326, 1200 m x 800 m (3:2):

| File | lat_min | lat_max | lon_min | lon_max |
|---|---|---|---|---|
| `P-042.jpg` | 5.726383 | 5.733617 | -6.527417 | -6.516583 |
| `P-041.jpg` | 5.701583 | 5.708817 | -6.729817 | -6.718983 |

The frames come from `app/web/maps.py` (`_window`). From Sentinel-2, credit: "contains modified
Copernicus Sentinel data" (the page prints it when an image is shown).
