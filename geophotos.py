# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Cairn Cartographics
"""GeoPhotos — main plugin class.

Adds a toolbar with:
  * "Load geotagged photos…"  — scan a folder (optionally recursively),
    read EXIF GPS, build a point layer, and save it to a GeoPackage.
  * "Identify photo" (checkable) — click a point to preview its image in a
    docked pane that fills 100% of the pane.
"""

import os

from qgis.PyQt.QtCore import Qt, QVariant, QSettings
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QProgressDialog, QMessageBox, QApplication
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsVectorFileWriter,
    QgsCoordinateReferenceSystem,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
    QgsCoordinateTransformContext,
    QgsSvgMarkerSymbolLayer,
    QgsMarkerSymbol,
    QgsSingleSymbolRenderer,
    QgsProperty,
    QgsEditorWidgetSetup,
    QgsMessageLog,
    Qgis,
)

from .exif_reader import read_gps
from .load_dialog import GeoPhotosDialog
from .photo_dock import PhotoDock
from .photo_maptool import PhotoIdentifyTool, PHOTO_LAYER_PROPERTY


def _log(exc):
    """Log a non-fatal exception to the QGIS message log (GeoPhotos tab)."""
    QgsMessageLog.logMessage(str(exc), "GeoPhotos", Qgis.Warning)


SETTINGS_THUMBS = "geophotos/thumbnails_enabled"
SETTINGS_ROWHEIGHT = "geophotos/row_height"
DEFAULT_ROW_HEIGHT = 400

SETTINGS_TIME_ENABLED = "geophotos/time_filter_enabled"
SETTINGS_TIME_FROM = "geophotos/time_filter_from"
SETTINGS_TIME_TO = "geophotos/time_filter_to"
DEFAULT_TIME_FROM = "2000-01-01 00:00:00"
DEFAULT_TIME_TO = "2100-12-31 23:59:59"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".tif", ".tiff")

# Custom-property keys used to remember scan settings on a layer, so the folder
# can be re-read later (even after a project reload).
PROP_FOLDER = "geophotos/folder"
PROP_SUBFOLDERS = "geophotos/subfolders"
PROP_GPKG = "geophotos/gpkg"
PROP_LAYERNAME = "geophotos/layername"


class GeoPhotosPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.toolbar = None
        self.dock = None
        self.map_tool = None
        self._prev_tool = None
        # Attribute-table thumbnail preferences, persisted across sessions.
        self.thumbnails_enabled = (
            QSettings().value(SETTINGS_THUMBS, False, type=bool)
        )
        self.row_height = (
            QSettings().value(SETTINGS_ROWHEIGHT, DEFAULT_ROW_HEIGHT, type=int)
        )
        # Capture-time range filter.
        self.time_filter_enabled = (
            QSettings().value(SETTINGS_TIME_ENABLED, False, type=bool)
        )
        self.time_from = QSettings().value(SETTINGS_TIME_FROM, DEFAULT_TIME_FROM, type=str)
        self.time_to = QSettings().value(SETTINGS_TIME_TO, DEFAULT_TIME_TO, type=str)

    # ------------------------------------------------------------------ setup
    def initGui(self):  # noqa: N802 (QGIS API name)
        def _icon(name):
            return QIcon(os.path.join(self.plugin_dir, name))

        self.toolbar = self.iface.addToolBar("GeoPhotos")
        self.toolbar.setObjectName("GeoPhotosToolbar")

        # Settings action (gear) — opens the load / refresh / settings dialog.
        self.settings_action = QAction(_icon("icon_settings.svg"), "GeoPhotos (load / refresh photos)…", self.iface.mainWindow())
        self.settings_action.triggered.connect(self.open_settings)
        self.toolbar.addAction(self.settings_action)
        self.iface.addPluginToMenu("&GeoPhotos", self.settings_action)
        self.actions.append(self.settings_action)

        # Identify action (orange camera + pointer cursor), checkable — retained.
        self.identify_action = QAction(_icon("icon_identify.svg"), "Identify photo (click a point)", self.iface.mainWindow())
        self.identify_action.setCheckable(True)
        self.identify_action.toggled.connect(self.toggle_identify)
        self.toolbar.addAction(self.identify_action)
        self.iface.addPluginToMenu("&GeoPhotos", self.identify_action)
        self.actions.append(self.identify_action)

        # Dock
        self.dock = PhotoDock(self.iface.mainWindow())
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()

        # Map tool
        self.map_tool = PhotoIdentifyTool(self.canvas, self._on_photo_selected)
        self.map_tool.setAction(self.identify_action)

    def unload(self):
        if self.map_tool and self.canvas.mapTool() == self.map_tool:
            self.canvas.unsetMapTool(self.map_tool)
        for action in self.actions:
            self.iface.removePluginMenu("&GeoPhotos", action)
        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.toolbar:
            del self.toolbar
            self.toolbar = None
        self.map_tool = None

    # ------------------------------------------------------------- identify
    def toggle_identify(self, checked):
        if checked:
            self._prev_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self.map_tool)
            if self.dock:
                self.dock.show()
                self.dock.raise_()
        else:
            if self.canvas.mapTool() == self.map_tool:
                self.canvas.unsetMapTool(self.map_tool)

    def _on_photo_selected(self, paths):
        if not self.dock:
            return
        self.dock.show()
        self.dock.raise_()
        # Accept either a single path or a list of paths (a clicked cluster).
        if isinstance(paths, (list, tuple)):
            self.dock.show_photos(list(paths))
        else:
            self.dock.show_photos([paths])

    # ------------------------------------------------------------- settings
    def open_settings(self):
        """Open the combined Load / Refresh dialog."""
        dlg = GeoPhotosDialog(self, self.iface.mainWindow())
        dlg.exec_()

    def has_photo_layer(self):
        for lyr in QgsProject.instance().mapLayers().values():
            try:
                if lyr.customProperty(PROP_FOLDER):
                    return True
            except Exception as _exc:
                _log(_exc)
                continue
        return False

    def _photo_layers(self):
        out = []
        for lyr in QgsProject.instance().mapLayers().values():
            try:
                if lyr.customProperty(PHOTO_LAYER_PROPERTY) or lyr.customProperty(PROP_FOLDER):
                    out.append(lyr)
            except Exception as _exc:
                _log(_exc)
                continue
        return out

    def set_thumbnails(self, enabled):
        """Toggle image thumbnails for the 'path' field in the attribute table."""
        self.thumbnails_enabled = bool(enabled)
        QSettings().setValue(SETTINGS_THUMBS, self.thumbnails_enabled)
        for lyr in self._photo_layers():
            self._apply_thumbnail_widget(lyr)

    def set_row_height(self, height):
        """Set the thumbnail height (which drives the attribute-table row height)."""
        try:
            self.row_height = int(height)
        except Exception:
            return
        QSettings().setValue(SETTINGS_ROWHEIGHT, self.row_height)
        if self.thumbnails_enabled:
            for lyr in self._photo_layers():
                self._apply_thumbnail_widget(lyr)

    def selected_photo_paths(self):
        """File paths of all currently selected features across photo layers."""
        paths = []
        for lyr in self._photo_layers():
            try:
                idx = lyr.fields().indexFromName("path")
                if idx == -1:
                    continue
                for feat in lyr.selectedFeatures():
                    p = feat[idx]
                    if p:
                        paths.append(p)
            except Exception as _exc:
                _log(_exc)
                continue
        return paths

    def selected_photo_count(self):
        return len(self.selected_photo_paths())

    def export_selected_photos(self, dest_dir):
        """Copy the selected photos' files into dest_dir. Shows a summary."""
        import shutil
        paths = self.selected_photo_paths()
        if not paths:
            self._msg("No photos are selected.", warn=True)
            return
        copied = 0
        missing = 0
        errors = 0
        used = set()
        for p in paths:
            if not os.path.exists(p):
                missing += 1
                continue
            base = os.path.basename(p)
            target = os.path.join(dest_dir, base)
            # Avoid overwriting files with the same name from different folders.
            if target in used or os.path.exists(target):
                stem, ext = os.path.splitext(base)
                i = 1
                while True:
                    cand = os.path.join(dest_dir, "%s_%d%s" % (stem, i, ext))
                    if cand not in used and not os.path.exists(cand):
                        target = cand
                        break
                    i += 1
            try:
                shutil.copy2(p, target)
                used.add(target)
                copied += 1
            except Exception:
                errors += 1

        parts = ["Copied %d photo(s) to:\n%s" % (copied, dest_dir)]
        if missing:
            parts.append("%d file(s) were missing on disk." % missing)
        if errors:
            parts.append("%d file(s) failed to copy." % errors)
        self._msg("\n\n".join(parts))

    def set_time_filter(self, enabled, from_str, to_str):
        """Enable/disable a capture-time range filter on all photo layers."""
        self.time_filter_enabled = bool(enabled)
        if from_str:
            self.time_from = from_str
        if to_str:
            self.time_to = to_str
        QSettings().setValue(SETTINGS_TIME_ENABLED, self.time_filter_enabled)
        QSettings().setValue(SETTINGS_TIME_FROM, self.time_from)
        QSettings().setValue(SETTINGS_TIME_TO, self.time_to)
        for lyr in self._photo_layers():
            self._apply_time_filter(lyr)
        self.canvas.refresh()

    def _apply_time_filter(self, layer):
        """Filter the layer to photos whose 'datetime' is within the range."""
        try:
            if self.time_filter_enabled:
                expr = (
                    "\"datetime\" >= '%s' AND \"datetime\" <= '%s'"
                    % (self.time_from, self.time_to)
                )
                layer.setSubsetString(expr)
            else:
                layer.setSubsetString("")
        except Exception as _exc:
            _log(_exc)

    def _apply_thumbnail_widget(self, layer):
        """Configure the 'path' field as an image attachment (thumbnails) or plain text."""
        try:
            idx = layer.fields().indexFromName("path")
            if idx == -1:
                return
            if self.thumbnails_enabled:
                cfg = {
                    "DocumentViewer": 1,                 # 1 = Image
                    "DocumentViewerHeight": int(self.row_height),
                    "DocumentViewerWidth": 0,            # 0 = auto width
                    "FileWidget": True,
                    "FileWidgetButton": True,
                    "RelativeStorage": 0,                # absolute paths
                }
                setup = QgsEditorWidgetSetup("ExternalResource", cfg)
            else:
                setup = QgsEditorWidgetSetup("TextEdit", {})
            layer.setEditorWidgetSetup(idx, setup)
        except Exception as _exc:
            _log(_exc)

    # ----------------------------------------------------------------- load
    def _do_load(self, v):
        """Run a load from dialog values. Returns True on success."""
        if not v["folder"] or not os.path.isdir(v["folder"]):
            self._msg("Please choose a valid photo folder.", warn=True)
            return False
        if not v["gpkg"]:
            self._msg("Please choose an output GeoPackage path.", warn=True)
            return False

        files = self._scan_files(v["folder"], v["subfolders"])
        if not files:
            self._msg("No JPEG/TIFF images found in the selected folder.", warn=True)
            return False

        features, scanned, geotagged = self._build_features(files)
        if not features:
            self._msg(
                "Scanned %d image(s) but none contained GPS coordinates."
                % scanned, warn=True,
            )
            return False

        layer = self._write_gpkg(features, v["gpkg"], v["layer"], allow_overwrite=v["overwrite"])
        if layer is None:
            return False

        # Tag, remember scan settings, style and load.
        layer.setCustomProperty(PHOTO_LAYER_PROPERTY, "1")
        layer.setCustomProperty(PROP_FOLDER, v["folder"])
        layer.setCustomProperty(PROP_SUBFOLDERS, "1" if v["subfolders"] else "0")
        layer.setCustomProperty(PROP_GPKG, v["gpkg"])
        layer.setCustomProperty(PROP_LAYERNAME, v["layer"])
        self._apply_icon_style(layer)
        self._apply_thumbnail_widget(layer)
        self._apply_time_filter(layer)
        QgsProject.instance().addMapLayer(layer)
        try:
            self.canvas.setExtent(layer.extent())
            self.canvas.refresh()
        except Exception as _exc:
            _log(_exc)

        # Auto-enable the identify tool and dock.
        self.identify_action.setChecked(True)

        self.iface.messageBar().pushMessage(
            "GeoPhotos",
            "Loaded %d geotagged photo(s) of %d scanned into '%s'."
            % (geotagged, scanned, os.path.basename(v["gpkg"])),
            level=Qgis.Success,
            duration=8,
        )
        return True

    # --------------------------------------------------------------- re-read
    def run_reread(self):
        """Rescan the folder saved on the active photo layer and update its GPKG."""
        layer = self._find_reread_layer()
        if layer is None:
            self._msg(
                "No GeoPhotos layer found to re-read.\n\n"
                "Select a layer created by GeoPhotos (or run "
                "“Load geotagged photos…” first), then try again.",
                warn=True,
            )
            return

        folder = layer.customProperty(PROP_FOLDER)
        subfolders = str(layer.customProperty(PROP_SUBFOLDERS)) == "1"
        gpkg = layer.customProperty(PROP_GPKG)
        layer_name = layer.customProperty(PROP_LAYERNAME) or layer.name()

        if not folder or not os.path.isdir(folder):
            self._msg(
                "The original photo folder for this layer no longer exists:\n%s"
                % folder, warn=True,
            )
            return
        if not gpkg:
            self._msg("This layer has no stored GeoPackage path to update.", warn=True)
            return

        # Photo count before the rescan, to report how many were added.
        try:
            previous_count = layer.featureCount()
        except Exception:
            previous_count = 0

        files = self._scan_files(folder, subfolders)
        if not files:
            self._msg("No JPEG/TIFF images found in:\n%s" % folder, warn=True)
            return

        features, scanned, geotagged = self._build_features(files)
        if not features:
            self._msg(
                "Scanned %d image(s) but none contained GPS coordinates." % scanned,
                warn=True,
            )
            return

        # Overwrite the layer in place; _write_gpkg removes the old loaded layer
        # (releasing its lock) and returns a fresh one to re-add.
        new_layer = self._write_gpkg(features, gpkg, layer_name, allow_overwrite=True)
        if new_layer is None:
            return

        new_layer.setCustomProperty(PHOTO_LAYER_PROPERTY, "1")
        new_layer.setCustomProperty(PROP_FOLDER, folder)
        new_layer.setCustomProperty(PROP_SUBFOLDERS, "1" if subfolders else "0")
        new_layer.setCustomProperty(PROP_GPKG, gpkg)
        new_layer.setCustomProperty(PROP_LAYERNAME, layer_name)
        self._apply_icon_style(new_layer)
        self._apply_thumbnail_widget(new_layer)
        self._apply_time_filter(new_layer)
        QgsProject.instance().addMapLayer(new_layer)
        self.canvas.refresh()

        delta = geotagged - (previous_count if previous_count and previous_count > 0 else 0)
        if delta > 0:
            change = "%d added" % delta
        elif delta < 0:
            change = "%d removed" % (-delta)
        else:
            change = "no change"
        self.iface.messageBar().pushMessage(
            "GeoPhotos",
            "Re-read folder: %d geotagged photo(s) total (%s); updated '%s'."
            % (geotagged, change, os.path.basename(gpkg)),
            level=Qgis.Success,
            duration=10,
        )

    def _find_reread_layer(self):
        """Return the active photo layer, or the first photo layer in the project."""
        active = self.iface.activeLayer()
        if active is not None and active.customProperty(PROP_FOLDER):
            return active
        for lyr in QgsProject.instance().mapLayers().values():
            try:
                if lyr.customProperty(PROP_FOLDER):
                    return lyr
            except Exception as _exc:
                _log(_exc)
                continue
        return None

    def _build_marker_symbol(self):
        """Camera SVG marker, rotated by EXIF azimuth (triangle = view direction)."""
        svg_path = os.path.join(self.plugin_dir, "icon_marker.svg")
        svg_layer = QgsSvgMarkerSymbolLayer(svg_path)
        svg_layer.setSize(8)  # mm
        symbol = QgsMarkerSymbol()
        symbol.changeSymbolLayer(0, svg_layer)
        symbol.setDataDefinedAngle(
            QgsProperty.fromExpression('coalesce("direction", 0) - 180')
        )
        return symbol

    def _apply_icon_style(self, layer):
        """Render points with the camera marker, rotated by EXIF azimuth."""
        try:
            layer.setRenderer(QgsSingleSymbolRenderer(self._build_marker_symbol()))
            layer.triggerRepaint()
        except Exception as _exc:
            _log(_exc)

    # --------------------------------------------------------------- helpers
    def _scan_files(self, folder, recursive):
        found = []
        if recursive:
            for root, _dirs, names in os.walk(folder):
                for n in names:
                    if n.lower().endswith(IMAGE_EXTENSIONS):
                        found.append(os.path.join(root, n))
        else:
            for n in os.listdir(folder):
                full = os.path.join(folder, n)
                if os.path.isfile(full) and n.lower().endswith(IMAGE_EXTENSIONS):
                    found.append(full)
        found.sort()
        return found

    def _build_features(self, files):
        fields = self._fields()
        # Parent the progress dialog to the currently active (modal) window —
        # usually the GeoPhotos dialog — so it appears ABOVE it, not behind.
        parent = QApplication.activeModalWidget() or QApplication.activeWindow() \
            or self.iface.mainWindow()
        progress = QProgressDialog(
            "Reading photo EXIF GPS…", "Cancel", 0, len(files), parent,
        )
        progress.setWindowTitle("GeoPhotos")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        progress.raise_()
        progress.activateWindow()
        QApplication.processEvents()

        features = []
        scanned = 0
        fid = 1
        for i, path in enumerate(files):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            QApplication.processEvents()
            scanned += 1
            try:
                gps = read_gps(path)
            except Exception:
                gps = None
            if not gps or "lat" not in gps or "lon" not in gps:
                continue

            feat = QgsFeature(fields)
            feat.setGeometry(
                QgsGeometry.fromPointXY(QgsPointXY(gps["lon"], gps["lat"]))
            )
            feat["fid"] = fid
            feat["filename"] = os.path.basename(path)
            feat["path"] = path
            feat["folder"] = os.path.dirname(path)
            feat["longitude"] = float(gps["lon"])
            feat["latitude"] = float(gps["lat"])
            feat["altitude"] = float(gps["altitude"]) if "altitude" in gps else None
            feat["direction"] = float(gps["direction"]) if "direction" in gps else None
            feat["datetime"] = gps.get("datetime")
            feat["camera_make"] = gps.get("make")
            feat["camera_model"] = gps.get("model")
            features.append(feat)
            fid += 1

        progress.setValue(len(files))
        return features, scanned, len(features)

    def _fields(self):
        fields = QgsFields()
        fields.append(QgsField("fid", QVariant.Int))
        fields.append(QgsField("filename", QVariant.String))
        fields.append(QgsField("path", QVariant.String))
        fields.append(QgsField("folder", QVariant.String))
        fields.append(QgsField("longitude", QVariant.Double))
        fields.append(QgsField("latitude", QVariant.Double))
        fields.append(QgsField("altitude", QVariant.Double))
        fields.append(QgsField("direction", QVariant.Double))
        fields.append(QgsField("datetime", QVariant.String))
        fields.append(QgsField("camera_make", QVariant.String))
        fields.append(QgsField("camera_model", QVariant.String))
        return fields

    def _release_layer_locks(self, gpkg_path, layer_name):
        """Remove any loaded layers pointing at this GPKG layer, freeing the
        file handle so it can be overwritten (network/lock safe)."""
        ids = []
        token = "layername=%s" % layer_name
        for lid, lyr in QgsProject.instance().mapLayers().items():
            try:
                src = lyr.source()
            except Exception as _exc:
                _log(_exc)
                continue
            if gpkg_path in src and token in src:
                ids.append(lid)
        if ids:
            QgsProject.instance().removeMapLayers(ids)
        return len(ids)

    def _write_gpkg(self, features, gpkg_path, layer_name, allow_overwrite=True):
        """Write features as a layer inside a GeoPackage and return it (not added).

        Never deletes the .gpkg file. If the file already exists, the named
        layer is overwritten in place (CreateOrOverwriteLayer) — which works on
        network/external volumes and when the file is currently open. Any loaded
        layer using this layer is removed first to release its lock.
        """
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
        fields = self._fields()
        uri = "%s|layername=%s" % (gpkg_path, layer_name)
        file_exists = os.path.exists(gpkg_path)

        # If not allowed to overwrite and the layer already exists, stop.
        if file_exists and not allow_overwrite:
            test = QgsVectorLayer(uri, "t", "ogr")
            if test.isValid():
                self._msg(
                    "A layer named '%s' already exists in that GeoPackage.\n\n"
                    "Tick 'Overwrite GeoPackage if it already exists', or choose "
                    "a different layer name or output file." % layer_name,
                    warn=True,
                )
                return None

        # Release any open handle on this layer before writing.
        self._release_layer_locks(gpkg_path, layer_name)

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = layer_name
        options.fileEncoding = "UTF-8"
        options.actionOnExistingFile = (
            QgsVectorFileWriter.CreateOrOverwriteLayer if file_exists
            else QgsVectorFileWriter.CreateOrOverwriteFile
        )

        transform_context = QgsProject.instance().transformContext() \
            if QgsProject.instance() else QgsCoordinateTransformContext()

        try:
            writer = QgsVectorFileWriter.create(
                gpkg_path,
                fields,
                QgsWkbTypes.Point,
                crs,
                transform_context,
                options,
            )
        except Exception as exc:  # older API signature fallback
            self._msg("Could not create GeoPackage: %s" % exc, warn=True)
            return None

        if writer.hasError() != QgsVectorFileWriter.NoError:
            self._msg("GeoPackage write error: %s" % writer.errorMessage(), warn=True)
            del writer
            return None

        for feat in features:
            writer.addFeature(feat)
        del writer  # flush & close

        uri = "%s|layername=%s" % (gpkg_path, layer_name)
        layer = QgsVectorLayer(uri, layer_name, "ogr")
        if not layer.isValid():
            self._msg("Saved GeoPackage but could not load the layer.", warn=True)
            return None
        return layer

    def _msg(self, text, warn=False):
        if warn:
            QMessageBox.warning(self.iface.mainWindow(), "GeoPhotos", text)
        else:
            QMessageBox.information(self.iface.mainWindow(), "GeoPhotos", text)
