# -*- coding: utf-8 -*-
"""GeoPhotos QGIS plugin entry point."""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    """Load the GeoPhotos plugin class.

    :param iface: A QGIS interface instance.
    """
    from .geophotos import GeoPhotosPlugin
    return GeoPhotosPlugin(iface)
