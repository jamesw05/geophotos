# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Cairn Cartographics
"""Docked photo preview pane.

Shows a single image scaled to fill the dock at 100% (largest size that fits
while preserving aspect ratio). Rescales automatically when the dock is
resized. A checkable "Zoom" tool lets the user drag a rectangle over the photo
to zoom into that region (double-click resets to the full image). Also offers
an "Open externally" action.
"""

import os
import sys
import subprocess  # nosec B404 (only used to open a validated local file, no shell)

from qgis.PyQt.QtCore import Qt, QUrl, QTimer, QRect, QRectF, QPoint, QSize
from qgis.PyQt.QtGui import QPixmap, QImageReader, QTransform, QDesktopServices

from .exif_reader import read_orientation
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QSizePolicy,
    QRubberBand,
)


class _ImageLabel(QLabel):
    """A QLabel that renders (a region of) its pixmap scaled to fill itself.

    The visible region is tracked as a *fractional* rectangle of the image, so
    the underlying pixmap can be swapped between a fast downscaled preview and
    the full-resolution image without disturbing the current zoom. Zoom mode
    turns the cursor into a crosshair; dragging a box zooms into that area and
    double-click resets to the full image.
    """

    PREVIEW_CAP = 2048  # longest-side pixels for the fast preview

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = None
        self._source = None          # currently displayed QPixmap
        self._full_loaded = False    # True once full-resolution is in _source
        self._view = QRectF(0, 0, 1, 1)  # fractional region of the image shown
        self._disp = QRect()         # where the scaled pixmap sits in the label
        self._src_rect = QRect()     # pixel region of _source currently shown
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._zoom_mode = False
        self._rubber = QRubberBand(QRubberBand.Rectangle, self)
        self._origin = QPoint()

        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setStyleSheet("background:#1e1e1e; color:#bbbbbb;")
        self.setText("Click a photo point on the map to preview it here.")

    # ---------------------------------------------------------------- image
    def _read_pixmap(self, path, scaled_cap=None):
        """Load a pixmap, applying EXIF orientation; optionally downscaled."""
        reader = QImageReader(path)
        if scaled_cap:
            size = reader.size()
            if size.isValid():
                w, h = size.width(), size.height()
                if max(w, h) > scaled_cap:
                    if w >= h:
                        reader.setScaledSize(QSize(scaled_cap, max(1, round(scaled_cap * h / w))))
                    else:
                        reader.setScaledSize(QSize(max(1, round(scaled_cap * w / h)), scaled_cap))
        img = reader.read()
        if img.isNull():
            pm = QPixmap(path)  # fallback
        else:
            pm = QPixmap.fromImage(img)
        if pm.isNull():
            return pm
        return self._apply_orientation(pm, path)

    @staticmethod
    def _apply_orientation(pm, path):
        """Rotate/flip a pixmap per its EXIF orientation so portraits stand up."""
        try:
            o = read_orientation(path)
        except Exception:
            o = 1
        if o == 1:
            return pm
        t = QTransform()
        if o == 2:
            t.scale(-1, 1)
        elif o == 3:
            t.rotate(180)
        elif o == 4:
            t.scale(1, -1)
        elif o == 5:
            t.rotate(90); t.scale(1, -1)
        elif o == 6:
            t.rotate(90)
        elif o == 7:
            t.rotate(270); t.scale(1, -1)
        elif o == 8:
            t.rotate(270)
        else:
            return pm
        return pm.transformed(t, Qt.SmoothTransformation)

    def set_image(self, path):
        self._path = path
        self._view = QRectF(0, 0, 1, 1)
        self._full_loaded = False
        if not path:
            self._source = None
            self.setText("No image.")
            return
        # If the user is already in zoom mode, load full-res straight away;
        # otherwise load a fast preview.
        if self._zoom_mode:
            pm = self._read_pixmap(path)
            self._full_loaded = True
        else:
            pm = self._read_pixmap(path, scaled_cap=self.PREVIEW_CAP)
        if pm.isNull():
            self._source = None
            self.setText("Unable to load image:\n%s" % path)
            return
        self._source = pm
        self._rescale()

    def load_full_resolution(self):
        """Swap the preview for the full-resolution image (keeps current zoom)."""
        if self._path and not self._full_loaded:
            pm = self._read_pixmap(self._path)
            if not pm.isNull():
                self._source = pm
                self._full_loaded = True
                self._rescale()

    def clear_image(self):
        self._source = None
        self._path = None
        self._full_loaded = False
        self._view = QRectF(0, 0, 1, 1)
        self.setText("Click a photo point on the map to preview it here.")

    def reset_zoom(self):
        if self._source is not None:
            self._view = QRectF(0, 0, 1, 1)
            self._rescale()

    def set_zoom_mode(self, enabled):
        self._zoom_mode = bool(enabled)
        self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)
        if enabled:
            # Ensure high-resolution pixels are available for zooming.
            self.load_full_resolution()
        elif self._rubber.isVisible():
            self._rubber.hide()

    # --------------------------------------------------------------- render
    def _rescale(self):
        if self._source is None or self._source.isNull():
            return
        sw, sh = self._source.width(), self._source.height()
        rx = int(round(self._view.x() * sw))
        ry = int(round(self._view.y() * sh))
        rw = max(1, int(round(self._view.width() * sw)))
        rh = max(1, int(round(self._view.height() * sh)))
        src_rect = QRect(rx, ry, rw, rh).intersected(QRect(0, 0, sw, sh))
        if src_rect.width() < 1 or src_rect.height() < 1:
            src_rect = QRect(0, 0, sw, sh)
        self._src_rect = src_rect

        cropped = self._source.copy(src_rect)
        scaled = cropped.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setPixmap(scaled)
        off_x = max(0, (self.width() - scaled.width()) // 2)
        off_y = max(0, (self.height() - scaled.height()) // 2)
        self._disp = QRect(off_x, off_y, scaled.width(), scaled.height())
        self._scale_x = scaled.width() / src_rect.width() if src_rect.width() else 1.0
        self._scale_y = scaled.height() / src_rect.height() if src_rect.height() else 1.0

    def resizeEvent(self, event):  # noqa: N802 (Qt API name)
        super().resizeEvent(event)
        self._rescale()

    # --------------------------------------------------------------- mouse
    def mousePressEvent(self, event):  # noqa: N802
        if self._zoom_mode and self._source is not None and event.button() == Qt.LeftButton:
            self._origin = event.pos()
            self._rubber.setGeometry(QRect(self._origin, QSize()))
            self._rubber.show()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._zoom_mode and self._rubber.isVisible():
            self._rubber.setGeometry(QRect(self._origin, event.pos()).normalized())
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._zoom_mode and self._rubber.isVisible():
            self._rubber.hide()
            sel = QRect(self._origin, event.pos()).normalized()
            self._apply_zoom(sel)
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        # Double-click anywhere on the image resets to the full view.
        self.reset_zoom()
        super().mouseDoubleClickEvent(event)

    def _apply_zoom(self, sel_label):
        if self._source is None:
            return
        sel = sel_label.intersected(self._disp)
        if sel.width() < 6 or sel.height() < 6:
            return  # treat as a stray click, not a zoom box
        sw, sh = self._source.width(), self._source.height()
        # Label coords -> source pixels -> fractional view.
        px = self._src_rect.x() + (sel.x() - self._disp.x()) / self._scale_x
        py = self._src_rect.y() + (sel.y() - self._disp.y()) / self._scale_y
        pw = sel.width() / self._scale_x
        ph = sel.height() / self._scale_y
        new = QRectF(px / sw, py / sh, pw / sw, ph / sh).intersected(QRectF(0, 0, 1, 1))
        if new.width() > 0.001 and new.height() > 0.001:
            self._view = new
            self._rescale()


class PhotoDock(QDockWidget):
    """Dock widget holding the fill-to-100% image pane."""

    def __init__(self, parent=None):
        super().__init__("Geophotos", parent)
        self.setObjectName("GeoPhotosPreviewDock")
        self.setAllowedAreas(Qt.AllDockWidgetAreas)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header: "Photo Filepath:" title, buttons, and the full path below.
        header = QWidget()
        hv = QVBoxLayout(header)
        hv.setContentsMargins(6, 4, 6, 4)
        hv.setSpacing(1)

        # Nav row (above the filepath): "photos at clicked location: n / N" with
        # the Prev/Next arrows side by side on the right.
        nav = QHBoxLayout()
        self._counter_label = QLabel("")
        nav.addWidget(self._counter_label, 0)

        self._prev_btn = QToolButton()
        self._prev_btn.setText("◀")
        self._prev_btn.setToolTip("Previous photo at this location")
        self._prev_btn.setEnabled(False)
        self._prev_btn.clicked.connect(self._show_prev)
        nav.addWidget(self._prev_btn, 0)

        self._next_btn = QToolButton()
        self._next_btn.setText("▶")
        self._next_btn.setToolTip("Next photo at this location")
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self._show_next)
        nav.addWidget(self._next_btn, 0)
        nav.addStretch(1)
        hv.addLayout(nav)

        top = QHBoxLayout()
        title = QLabel("Photo Filepath:")
        title.setStyleSheet("font-weight:bold;")
        top.addWidget(title, 1)

        self._zoom_btn = QToolButton()
        self._zoom_btn.setText("Zoom")
        self._zoom_btn.setCheckable(True)
        self._zoom_btn.setToolTip(
            "Zoom: drag a box on the photo to zoom in. Double-click the photo to reset."
        )
        self._zoom_btn.setEnabled(False)
        self._zoom_btn.toggled.connect(self._on_zoom_toggled)
        top.addWidget(self._zoom_btn, 0)

        self._zoom_full_btn = QToolButton()
        self._zoom_full_btn.setText("Zoom full")
        self._zoom_full_btn.setToolTip("Reset the photo to the full image")
        self._zoom_full_btn.setEnabled(False)
        self._zoom_full_btn.clicked.connect(self._image_reset_zoom)
        top.addWidget(self._zoom_full_btn, 0)

        self._open_btn = QToolButton()
        self._open_btn.setText("Open externally")
        self._open_btn.setToolTip("Open this photo in the system image viewer")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._open_external)
        top.addWidget(self._open_btn, 0)
        hv.addLayout(top)

        self._path_label = QLabel("")
        self._path_label.setWordWrap(True)
        self._path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hv.addWidget(self._path_label)
        layout.addWidget(header, 0)

        # Image pane (fills remaining space -> 100%)
        self._image = _ImageLabel()
        layout.addWidget(self._image, 1)

        self.setWidget(container)
        self._current_path = None
        self._photos = []
        self._index = 0

    def show_photo(self, path):
        """Display a single image path (kept for backward compatibility)."""
        self.show_photos([path])

    def show_photos(self, paths):
        """Display one or more image paths; multiple become a Prev/Next set."""
        self._photos = [p for p in (paths or []) if p]
        self._index = 0
        if not self._photos:
            self._current_path = None
            self._path_label.setText("No photo.")
            self._image.clear_image()
            for b in (self._open_btn, self._zoom_btn, self._zoom_full_btn):
                b.setEnabled(False)
            self._zoom_btn.setChecked(False)
            self._update_nav()
            return
        self._show_index(0)

    def _show_index(self, i):
        self._index = max(0, min(i, len(self._photos) - 1))
        path = self._photos[self._index]
        self._current_path = path
        if path and os.path.exists(path):
            self._path_label.setText(path)
            self._image.set_image(path)
            self._open_btn.setEnabled(True)
            self._zoom_btn.setEnabled(True)
            self._zoom_full_btn.setEnabled(True)
        else:
            self._path_label.setText(path or "File not found")
            self._image.clear_image()
            self._open_btn.setEnabled(False)
            self._zoom_btn.setEnabled(False)
            self._zoom_btn.setChecked(False)
            self._zoom_full_btn.setEnabled(False)
        self._update_nav()

    def _update_nav(self):
        n = len(self._photos)
        multi = n > 1
        self._prev_btn.setEnabled(multi and self._index > 0)
        self._next_btn.setEnabled(multi and self._index < n - 1)
        self._counter_label.setText(
            "Photos at clicked location: %d / %d" % (self._index + 1, n) if n else ""
        )

    def _show_prev(self):
        if self._index > 0:
            self._show_index(self._index - 1)

    def _show_next(self):
        if self._index < len(self._photos) - 1:
            self._show_index(self._index + 1)

    def _on_zoom_toggled(self, checked):
        self._image.set_zoom_mode(checked)

    def _image_reset_zoom(self):
        self._image.reset_zoom()

    def _open_external(self):
        path = self._current_path
        if not path or not os.path.exists(path):
            return
        # Defer the launch so it runs AFTER Qt finishes processing this click
        # (which re-activates the QGIS window). Launching afterwards lets the
        # external viewer take focus on the first click instead of the second.
        QTimer.singleShot(60, lambda: self._launch(path))

    def _launch(self, path):
        # `path` is a validated, existing local file (checked in _open_external),
        # opened in the OS default viewer. No shell is used and arguments are a
        # fixed list, so there is no shell-injection surface.
        if not path or not os.path.exists(path):
            return
        try:
            if sys.platform == "darwin":
                # -a Preview activates the app and brings its window to front.
                subprocess.Popen(["/usr/bin/open", "-a", "Preview", path])  # nosec B603
            elif os.name == "nt":
                os.startfile(path)  # noqa: S606  # nosec B606 (Windows only)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except OSError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
