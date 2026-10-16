# GeoPhotos — QGIS plugin

This plugin loads a folder of geotagged photos onto the map and saves a GeoPackage of points referencing the photos. If new photos are added to the folder, there is a button to update the layer and geopackage with the new photos.
Clicking on a photo shows the photo in a docked pane. If there are multiple overlapping photos, they can be cycled in the pane. A zoom button can be used to zoom in for details using the full, original image. The image can also be opened in the default external photo viewer. 

Built for **QGIS 3.x** (PyQt5). No third-party Python packages are required —
EXIF GPS and orientation are read by a bundled, dependency-free parser.

## Toolbar

The plugin adds a **GeoPhotos** toolbar with two buttons:

- **⚙ GeoPhotos (gear)** — opens the GeoPhotos dialog (Load / Refresh /
  Settings / Export).
- **📷 Identify photo** (orange camera with a pointer) While active, the cursor is a pointing hand and
  clicking a photo point opens it in the docked viewer.

## The GeoPhotos dialog

### Load geotagged photos

Choose a **photo folder**, optionally tick **Include subfolders**, set the
**output GeoPackage** path and **layer name**, then click **Load photos**.

The plugin scans for `.jpg / .jpeg / .tif / .tiff`, reads each file's EXIF GPS,
and writes the ones that have a coordinate to a WGS84 (EPSG:4326) point layer
inside the GeoPackage. The map zooms to the new layer and the Identify tool is
enabled automatically. Photos without GPS are skipped and reported in the
count.

**Overwrite GeoPackage if it already exists** replaces the layer in place
inside the GeoPackage. The file itself is never deleted, so this works on
network/external volumes and when the layer is already loaded.

Each point carries these attributes: `fid`, `filename`, `path` (absolute),
`folder`, `longitude`, `latitude`, `altitude`, `direction` (image azimuth),
`datetime` (capture time, `YYYY-MM-DD HH:MM:SS`), `camera_make`,
`camera_model`.

### Refresh photo folder

**Refresh now** rescans the folder stored on the active (or first) GeoPhotos
layer and updates its GeoPackage with any added or removed photos, then
reloads it. The summary reports the total geotagged photos and how many were
added/removed.

### Settings

- **Show photo thumbnails in attribute table (form view)** — off by default.
  When on, the `path` field is configured as an image attachment so the photo
  renders in the attribute table's **Form view** (the Table/Form toggle at the
  bottom-right of the attribute table) and in each feature's form.
- **Thumbnail / row height (px)** — height of that image preview (default
  **400**). Enabled only when thumbnails are on.
- **Filter by capture time range** — when on, shows only photos whose EXIF
  capture time falls between the **From** and **To** datetimes. Applies to the
  map, the attribute table, and the Identify tool (it sets a layer filter).
  Photos without a capture time are hidden while the filter is on.

All Settings persist across sessions and apply live to every GeoPhotos layer.

### Export

 **Export selected photos…** asks for a destination folder and copies those photo files into it.
Missing files are skipped, and filename clashes (common across date
subfolders) are auto-renamed (`name_1.jpg`) so nothing is overwritten. A
summary reports copied / missing / failed counts.

## Map markers

Points are drawn with a red camera icon above a black triangle. The marker is
rotated on the map by the photo's EXIF image direction (azimuth), so the
triangle points the way the camera was facing.

## Photo preview dock ("Geophotos")

Clicking a photo point with the Identify tool opens the docked preview pane:

- **Photos at clicked location: n / N** with **◀ ▶** — because photos are
  often stacked at the same spot, a click collects every photo at that location
  and lets you step through them. All selected features are highlighted on the
  map.
- **Photo Filepath:** with the full path below it.
- The image fills the pane at 100% (largest fit, aspect preserved), rescaling
  when you resize the dock. Portrait photos are shown upright — EXIF
  orientation is applied.
- **Zoom** — drag a box on the photo to zoom into that area; loads the
  full-resolution image so the zoom is sharp. Double-click the photo to reset.
- **Zoom full** — reset to the full image.
- **Open externally** — open the photo in the system viewer.

## Install

1. In QGIS: *Plugins → Manage and Install Plugins → Install from ZIP*, and
   choose the `geophotos` ZIP. (Or copy the `geophotos` folder into your QGIS
   `python/plugins/` directory.)
2. Restart QGIS, and enable **GeoPhotos** under *Installed* if needed.

Plugin directories:
- **macOS:** `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
- **Windows:** `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
- **Linux:** `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | `classFactory` entry point |
| `metadata.txt` | Plugin metadata |
| `geophotos.py` | Main plugin: toolbar, load, refresh, GeoPackage writing, styling, thumbnails, time filter, export |
| `load_dialog.py` | The Load / Refresh / Settings / Export dialog |
| `exif_reader.py` | Dependency-free EXIF GPS + orientation parser |
| `photo_dock.py` | Preview dock: paging, zoom, EXIF-correct rendering |
| `photo_maptool.py` | Click-to-identify map tool (groups co-located photos) |
| `icon_settings.svg` | Toolbar gear icon |
| `icon_identify.svg` | Toolbar identify icon |
| `icon_marker.svg` | Map marker (camera + direction triangle) |
