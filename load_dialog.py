# -*- coding: utf-8 -*-
"""Combined GeoPhotos dialog: Load, Refresh, and Settings sections."""

import os

from qgis.PyQt.QtCore import QDateTime, QSettings
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QCheckBox,
    QPushButton,
    QFileDialog,
    QSpinBox,
    QDateTimeEdit,
    QMessageBox,
)

DT_FMT = "yyyy-MM-dd HH:mm:ss"

KEY_FOLDER = "geophotos/last_folder"
KEY_GPKG = "geophotos/last_gpkg"
KEY_LAYER = "geophotos/last_layer"


class GeoPhotosDialog(QDialog):
    """One dialog with three sections driving the plugin."""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self.setWindowTitle("GeoPhotos")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)

        layout.addWidget(self._build_load_section())
        layout.addWidget(self._build_refresh_section())
        layout.addWidget(self._build_settings_section())
        layout.addWidget(self._build_export_section())

        # Close button row
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    # ------------------------------------------------------- load section
    def _build_load_section(self):
        box = QGroupBox("Load geotagged photos")
        grid = QGridLayout(box)

        s = QSettings()
        last_folder = s.value(KEY_FOLDER, "", type=str)
        last_gpkg = s.value(KEY_GPKG, "", type=str)
        last_layer = s.value(KEY_LAYER, "photos", type=str)

        grid.addWidget(QLabel("Photo folder:"), 0, 0)
        self.folder_edit = QLineEdit(last_folder)
        grid.addWidget(self.folder_edit, 0, 1)
        browse_folder = QPushButton("Browse…")
        browse_folder.clicked.connect(self._pick_folder)
        grid.addWidget(browse_folder, 0, 2)

        self.subfolders_cb = QCheckBox("Include subfolders")
        self.subfolders_cb.setChecked(True)
        grid.addWidget(self.subfolders_cb, 1, 1)

        grid.addWidget(QLabel("Output GeoPackage:"), 2, 0)
        self.gpkg_edit = QLineEdit(last_gpkg)
        grid.addWidget(self.gpkg_edit, 2, 1)
        browse_gpkg = QPushButton("Browse…")
        browse_gpkg.clicked.connect(self._pick_gpkg)
        grid.addWidget(browse_gpkg, 2, 2)

        grid.addWidget(QLabel("Layer name:"), 3, 0)
        self.layer_edit = QLineEdit(last_layer or "photos")
        grid.addWidget(self.layer_edit, 3, 1)

        self.overwrite_cb = QCheckBox("Overwrite GeoPackage if it already exists")
        self.overwrite_cb.setChecked(True)
        grid.addWidget(self.overwrite_cb, 4, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.load_btn = QPushButton("Load photos")
        self.load_btn.setDefault(True)
        self.load_btn.clicked.connect(self._on_load)
        row.addWidget(self.load_btn)
        grid.addLayout(row, 5, 1, 1, 2)
        return box

    # ---------------------------------------------------- refresh section
    def _build_refresh_section(self):
        box = QGroupBox("Refresh photo folder")
        v = QVBoxLayout(box)
        info = QLabel(
            "Rescan the folder for the active (or first) GeoPhotos layer and "
            "update its GeoPackage with any added or removed photos."
        )
        info.setWordWrap(True)
        v.addWidget(info)

        row = QHBoxLayout()
        row.addStretch(1)
        self.refresh_btn = QPushButton("Refresh now")
        self.refresh_btn.clicked.connect(self._on_refresh)
        self.refresh_btn.setEnabled(self._plugin.has_photo_layer())
        if not self.refresh_btn.isEnabled():
            self.refresh_btn.setToolTip("Load photos first, or select a GeoPhotos layer.")
        row.addWidget(self.refresh_btn)
        v.addLayout(row)
        return box

    # --------------------------------------------------- settings section
    def _build_settings_section(self):
        box = QGroupBox("Settings")
        v = QVBoxLayout(box)

        self.thumbs_cb = QCheckBox("Show photo thumbnails in attribute table (form view)")
        self.thumbs_cb.setToolTip(
            "Render the photo as an image on the 'path' field. QGIS shows this in "
            "the attribute table's Form view (Table/Form toggle, bottom-right) and "
            "in each feature form — the grid view still shows the path as text. "
            "Off by default; can be slow for large photos over a network drive."
        )
        self.thumbs_cb.setChecked(bool(self._plugin.thumbnails_enabled))
        self.thumbs_cb.toggled.connect(self._on_thumbs_toggled)
        v.addWidget(self.thumbs_cb)

        hrow = QHBoxLayout()
        hrow.addWidget(QLabel("Thumbnail / row height (px):"))
        self.height_spin = QSpinBox()
        self.height_spin.setRange(16, 1000)
        self.height_spin.setSingleStep(5)
        self.height_spin.setValue(int(self._plugin.row_height))
        self.height_spin.setToolTip(
            "Height of the thumbnail, which sets how tall the attribute-table "
            "rows become. Default 400 px."
        )
        self.height_spin.setEnabled(self.thumbs_cb.isChecked())
        self.height_spin.valueChanged.connect(self._plugin.set_row_height)
        hrow.addWidget(self.height_spin)
        hrow.addStretch(1)
        v.addLayout(hrow)

        # --- Capture-time range filter ---
        self.time_cb = QCheckBox("Filter by capture time range")
        self.time_cb.setToolTip(
            "Show only photos whose EXIF capture time falls within the range "
            "below. Photos without a capture time are hidden while this is on."
        )
        self.time_cb.setChecked(bool(self._plugin.time_filter_enabled))
        self.time_cb.toggled.connect(self._on_time_toggled)
        v.addWidget(self.time_cb)

        trow = QHBoxLayout()
        trow.addWidget(QLabel("From:"))
        self.from_dt = QDateTimeEdit()
        self.from_dt.setDisplayFormat(DT_FMT)
        self.from_dt.setCalendarPopup(True)
        self.from_dt.setDateTime(self._parse_dt(self._plugin.time_from))
        self.from_dt.dateTimeChanged.connect(self._on_time_changed)
        trow.addWidget(self.from_dt)

        trow.addWidget(QLabel("To:"))
        self.to_dt = QDateTimeEdit()
        self.to_dt.setDisplayFormat(DT_FMT)
        self.to_dt.setCalendarPopup(True)
        self.to_dt.setDateTime(self._parse_dt(self._plugin.time_to))
        self.to_dt.dateTimeChanged.connect(self._on_time_changed)
        trow.addWidget(self.to_dt)
        trow.addStretch(1)
        v.addLayout(trow)

        self._update_time_enabled(self.time_cb.isChecked())
        return box

    # ----------------------------------------------------- export section
    def _build_export_section(self):
        box = QGroupBox("Export")
        v = QVBoxLayout(box)
        erow = QHBoxLayout()
        n = self._plugin.selected_photo_count()
        self.sel_label = QLabel("Selected photos: %d" % n)
        erow.addWidget(self.sel_label)
        erow.addStretch(1)
        self.export_btn = QPushButton("Export selected photos…")
        self.export_btn.setToolTip(
            "Copy the currently selected photos (e.g. a clicked location, or rows "
            "selected in the attribute table) into a folder you choose."
        )
        self.export_btn.clicked.connect(self._on_export)
        erow.addWidget(self.export_btn)
        v.addLayout(erow)
        return box

    def _on_export(self):
        n = self._plugin.selected_photo_count()
        self.sel_label.setText("Selected photos: %d" % n)
        if n == 0:
            QMessageBox.information(
                self, "GeoPhotos",
                "No photos are selected.\n\nClick a photo on the map, or select "
                "rows in the attribute table, then try again.",
            )
            return
        dest = QFileDialog.getExistingDirectory(self, "Choose export destination folder")
        if dest:
            self._plugin.export_selected_photos(dest)

    def _parse_dt(self, s):
        dt = QDateTime.fromString(s or "", DT_FMT)
        return dt if dt.isValid() else QDateTime.currentDateTime()

    def _on_thumbs_toggled(self, checked):
        self.height_spin.setEnabled(checked)
        self._plugin.set_thumbnails(checked)

    def _update_time_enabled(self, checked):
        self.from_dt.setEnabled(checked)
        self.to_dt.setEnabled(checked)

    def _on_time_toggled(self, checked):
        self._update_time_enabled(checked)
        self._apply_time()

    def _on_time_changed(self, *args):
        if self.time_cb.isChecked():
            self._apply_time()

    def _apply_time(self):
        self._plugin.set_time_filter(
            self.time_cb.isChecked(),
            self.from_dt.dateTime().toString(DT_FMT),
            self.to_dt.dateTime().toString(DT_FMT),
        )

    def done(self, result):
        """Persist the folder / GeoPackage / layer paths on any close."""
        s = QSettings()
        folder = self.folder_edit.text().strip()
        gpkg = self.gpkg_edit.text().strip()
        layer = self.layer_edit.text().strip()
        if folder:
            s.setValue(KEY_FOLDER, folder)
        if gpkg:
            s.setValue(KEY_GPKG, gpkg)
        if layer:
            s.setValue(KEY_LAYER, layer)
        super().done(result)

    # ----------------------------------------------------------- actions
    def _on_load(self):
        if self._plugin._do_load(self.load_values()):
            self.accept()

    def _on_refresh(self):
        self._plugin.run_reread()
        self.accept()

    def load_values(self):
        return {
            "folder": self.folder_edit.text().strip(),
            "subfolders": self.subfolders_cb.isChecked(),
            "gpkg": self.gpkg_edit.text().strip(),
            "layer": (self.layer_edit.text().strip() or "photos"),
            "overwrite": self.overwrite_cb.isChecked(),
        }

    # ----------------------------------------------------------- pickers
    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select photo folder")
        if path:
            self.folder_edit.setText(path)
            if not self.gpkg_edit.text():
                default = os.path.join(
                    os.path.dirname(path) or path,
                    (os.path.basename(path.rstrip("/\\")) or "photos") + "_photos.gpkg",
                )
                self.gpkg_edit.setText(default)

    def _pick_gpkg(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save GeoPackage as", self.gpkg_edit.text(),
            "GeoPackage (*.gpkg)",
        )
        if path:
            if not path.lower().endswith(".gpkg"):
                path += ".gpkg"
            self.gpkg_edit.setText(path)
