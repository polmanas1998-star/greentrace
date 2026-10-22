# Map backgrounds (optional)

The home page map draws the real 30 m cells (EU JRC GFC2020 forest, Hansen loss) on a dark ground.
If `<plot>.jpg` exists in this folder, `GET /api/plot-map` names it and the page shows it under the
cells, with an "Imagery" opacity slider on the map. Nothing is invented: without the file, the ground stays dark.

Each image must cover exactly its frame, north up, EPSG:4326, 1200 m x 800 m (3:2):

| File | lat_min | lat_max | lon_min | lon_max |
|---|---|---|---|---|
| `P-042.jpg` | 5.726383 | 5.733617 | -6.527417 | -6.516583 |
| `P-041.jpg` | 5.701583 | 5.708817 | -6.729817 | -6.718983 |

The frames come from `app/web/maps.py` (`_window`). From Sentinel-2, credit: "contains modified
Copernicus Sentinel data" (the page prints it when an image is shown).

## Satellite Lens (two dates)

`python -m scripts.fetch_satellite --before` adds, for each plot, the least cloudy clean frame of the
2019-2020 dry season (`<plot>_2020.jpg` and `.json`, same frame and pixel grid), then composes
`<plot>_lens.jpg` and `.json`: before | after side by side, 1024 x 336 px, the same 3 x 3 grid on
each half with A1-C3 small in the corners. That one image is what Nemotron Vision sees (app/lens).
Without it the Lens does not ask the model ("lens needs the 2020 scene") and shows the code's grid.
