# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Cairn Cartographics
"""Map tool that identifies clicked photo point(s) and previews the image(s).

Several photos are often taken at the same location, so their points stack on
top of each other. A click collects the whole co-located group (detected by
shared on-screen position) and hands the list to the dock, which lets the user
step through every photo at that spot.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QCursor
from qgis.core import (
    QgsRectangle,
    QgsFeatureRequest,
    QgsCoordinateTransform,
    QgsProject,
    QgsWkbTypes,
    QgsMessageLog,
    Qgis,
)
from qgis.gui import QgsMapTool

# Custom layer property used to tag layers created / recognised by this plugin.
PHOTO_LAYER_PROPERTY = "geophotos/photo_layer"
PATH_FIELD = "path"


def _log(exc):
    """Log a non-fatal exception to the QGIS message log (GeoPhotos tab)."""
    QgsMessageLog.logMessage(str(exc), "GeoPhotos", Qgis.Warning)


class PhotoIdentifyTool(QgsMapTool):
    """On click, find the clicked photo(s) and send their path(s) to the dock."""

    def __init__(self, canvas, on_photos_selected):
        super().__init__(canvas)
        self.canvas = canvas
        self._callback = on_photos_selected
        self.setCursor(QCursor(Qt.PointingHandCursor))

    def _photo_layers(self):
        layers = []
        for lyr in QgsProject.instance().mapLayers().values():
            try:
                if lyr.type() != lyr.VectorLayer:
                    continue
                if lyr.geometryType() != QgsWkbTypes.PointGeometry:
                    continue
                if lyr.customProperty(PHOTO_LAYER_PROPERTY):
                    layers.append(lyr)
                elif lyr.fields().indexFromName(PATH_FIELD) != -1:
                    layers.append(lyr)
            except Exception as _exc:
                _log(_exc)
                continue
        return layers

    def canvasReleaseEvent(self, event):  # noqa: N802 (Qt API name)
        click = event.pos()
        map_pt = self.toMapCoordinates(click)
        layers = self._photo_layers()
        if not layers:
            return

        mupp = self.canvas.mapUnitsPerPixel()
        tol = mupp * 12
        max_click_px = 16

        canvas_crs = self.canvas.mapSettings().destinationCrs()
        candidates = []  # (layer, feature, screen_x, screen_y)
        for lyr in layers:
            try:
                to_layer = QgsCoordinateTransform(canvas_crs, lyr.crs(), QgsProject.instance())
                lpt = to_layer.transform(map_pt)
            except Exception:
                lpt = map_pt
            rect = QgsRectangle(lpt.x() - tol, lpt.y() - tol, lpt.x() + tol, lpt.y() + tol)
            try:
                to_canvas = QgsCoordinateTransform(lyr.crs(), canvas_crs, QgsProject.instance())
            except Exception:
                to_canvas = None
            for feat in lyr.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
                geom = feat.geometry()
                if geom.isEmpty():
                    continue
                if geom.type() == QgsWkbTypes.PointGeometry:
                    fp = geom.asPoint()
                else:
                    fp = geom.centroid().asPoint()
                try:
                    mp = to_canvas.transform(fp) if to_canvas else fp
                except Exception:
                    mp = fp
                sp = self.toCanvasCoordinates(mp)  # QPoint in canvas pixels
                candidates.append((lyr, feat, sp.x(), sp.y()))

        if not candidates:
            return

        # Group candidates by shared on-screen position (co-located photos land
        # within a couple of pixels of each other at their TRUE location).
        groups = []  # each: {"cx","cy","items":[(layer,feat)]}
        for lyr, feat, sx, sy in candidates:
            placed = False
            for grp in groups:
                if abs(sx - grp["cx"]) <= 6 and abs(sy - grp["cy"]) <= 6:
                    grp["items"].append((lyr, feat))
                    placed = True
                    break
            if not placed:
                groups.append({"cx": sx, "cy": sy, "items": [(lyr, feat)]})

        # Choose the group whose centre is closest to the click.
        def gdist(grp):
            return (grp["cx"] - click.x()) ** 2 + (grp["cy"] - click.y()) ** 2

        best = min(groups, key=gdist)
        if gdist(best) ** 0.5 > max_click_px:
            return  # clicked empty space

        items = best["items"]
        items.sort(key=lambda c: c[1].id())  # stable order

        # Build path list and selection.
        paths = []
        sel = {}
        for lyr, feat in items:
            idx = lyr.fields().indexFromName(PATH_FIELD)
            path = feat[idx] if idx != -1 else None
            if path:
                paths.append(path)
                sel.setdefault(lyr, []).append(feat.id())
        for lyr, ids in sel.items():
            try:
                lyr.removeSelection()
                lyr.selectByIds(ids)
            except Exception as _exc:
                _log(_exc)

        if paths:
            self._callback(paths)
