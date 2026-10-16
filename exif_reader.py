# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Cairn Cartographics
"""Dependency-free EXIF GPS reader.

Parses the EXIF/TIFF metadata block of JPEG and TIFF images to extract GPS
coordinates and a few useful attributes, without requiring Pillow or any other
third-party library. Only the standard library is used.

Public API::

    read_gps(path) -> dict | None

Returns a dict with keys (any of which may be absent):
    lon, lat            -- decimal degrees (WGS84), signed
    altitude            -- metres, signed (negative = below sea level)
    direction           -- image direction in degrees (0-360)
    datetime            -- 'YYYY-MM-DD HP:MM:SS' capture time if available
    make, model         -- camera make / model strings

Returns None if the file has no usable GPS fix or cannot be parsed.
"""

import struct

# Narrow set of errors that EXIF parsing may legitimately raise on malformed
# input. Catching these (rather than a broad Exception) keeps the parser robust
# without masking unrelated bugs.
_PARSE_ERRORS = (ValueError, TypeError, IndexError, KeyError, AttributeError, struct.error)

# ---------------------------------------------------------------------------
# TIFF field types -> (size in bytes, struct code for a single element)
# ---------------------------------------------------------------------------
_TYPE_SIZES = {
    1: (1, "B"),   # BYTE
    2: (1, "c"),   # ASCII
    3: (2, "H"),   # SHORT
    4: (4, "I"),   # LONG
    5: (8, "II"),  # RATIONAL (num, den)
    6: (1, "b"),   # SBYTE
    7: (1, "c"),   # UNDEFINED
    8: (2, "h"),   # SSHORT
    9: (4, "i"),   # SLONG
    10: (8, "ii"),  # SRATIONAL (num, den)
    11: (4, "f"),  # FLOAT
    12: (8, "d"),  # DOUBLE
}

# GPS IFD tags we care about
_GPS_TAGS = {
    1: "lat_ref",
    2: "lat",
    3: "lon_ref",
    4: "lon",
    5: "alt_ref",
    6: "alt",
    16: "dir_ref",
    17: "direction",
    29: "date_stamp",
}


class _Reader:
    """Reads TIFF IFD structures from a bytes buffer starting at the TIFF header."""

    def __init__(self, data):
        self.data = data
        # Byte order
        bom = data[:2]
        if bom == b"II":
            self.e = "<"
        elif bom == b"MM":
            self.e = ">"
        else:
            raise ValueError("not a TIFF header")
        magic = struct.unpack(self.e + "H", data[2:4])[0]
        if magic != 42:
            raise ValueError("bad TIFF magic")
        self.first_ifd = struct.unpack(self.e + "I", data[4:8])[0]

    def _u16(self, off):
        return struct.unpack(self.e + "H", self.data[off:off + 2])[0]

    def _u32(self, off):
        return struct.unpack(self.e + "I", self.data[off:off + 4])[0]

    def read_ifd(self, offset):
        """Return {tag: (type, count, value_bytes)} for one IFD."""
        entries = {}
        if offset + 2 > len(self.data):
            return entries
        count = self._u16(offset)
        pos = offset + 2
        for _ in range(count):
            if pos + 12 > len(self.data):
                break
            tag = self._u16(pos)
            ftype = self._u16(pos + 2)
            num = self._u32(pos + 4)
            value_field = self.data[pos + 8:pos + 12]
            size_info = _TYPE_SIZES.get(ftype)
            if size_info:
                total = size_info[0] * num
                if total <= 4:
                    raw = value_field[:total]
                else:
                    ptr = struct.unpack(self.e + "I", value_field)[0]
                    raw = self.data[ptr:ptr + total]
            else:
                raw = value_field
            entries[tag] = (ftype, num, raw)
            pos += 12
        return entries

    def decode(self, ftype, count, raw):
        """Decode a raw field into a python value / list."""
        info = _TYPE_SIZES.get(ftype)
        if ftype == 2:  # ASCII
            return raw.split(b"\x00", 1)[0].decode("latin-1", "replace")
        if not info:
            return raw
        size, code = info
        vals = []
        for i in range(count):
            chunk = raw[i * size:(i + 1) * size]
            if len(chunk) < size:
                break
            if ftype in (5, 10):  # rational
                n, d = struct.unpack(self.e + code, chunk)
                vals.append((n / d) if d else 0.0)
            else:
                vals.append(struct.unpack(self.e + code, chunk)[0])
        if len(vals) == 1:
            return vals[0]
        return vals


def _dms_to_deg(dms, ref):
    """Convert [deg, min, sec] + hemisphere ref to signed decimal degrees."""
    try:
        d, m, s = (dms + [0, 0, 0])[:3] if isinstance(dms, list) else (dms, 0, 0)
    except Exception:
        return None
    deg = float(d) + float(m) / 60.0 + float(s) / 3600.0
    if ref and ref.upper() in ("S", "W"):
        deg = -deg
    return deg


def _find_exif_block(path):
    """Return the bytes of the TIFF/EXIF block for a JPEG or TIFF file."""
    with open(path, "rb") as fh:
        head = fh.read(4)
        fh.seek(0)
        # Native TIFF
        if head[:2] in (b"II", b"MM"):
            return fh.read()
        # JPEG: locate APP1 (0xFFE1) segment carrying "Exif\0\0"
        if head[:2] == b"\xff\xd8":
            fh.seek(2)
            while True:
                marker = fh.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                m = marker[1]
                if m in (0xD9, 0xDA):  # EOI or start of scan -> no more metadata
                    return None
                seg_len_bytes = fh.read(2)
                if len(seg_len_bytes) < 2:
                    return None
                seg_len = struct.unpack(">H", seg_len_bytes)[0]
                payload = fh.read(seg_len - 2)
                if m == 0xE1 and payload[:6] == b"Exif\x00\x00":
                    return payload[6:]
            # unreachable
    return None


def read_orientation(path):
    """Return the EXIF orientation value (1-8) for an image, or 1 if absent.

    1 = normal, 3 = 180deg, 6 = rotate 90 CW, 8 = rotate 270 CW; 2/4/5/7 are
    mirrored variants.
    """
    try:
        block = _find_exif_block(path)
        if not block:
            return 1
        r = _Reader(block)
        ifd0 = r.read_ifd(r.first_ifd)
        if 0x0112 in ifd0:
            val = r.decode(*ifd0[0x0112])
            if isinstance(val, list):
                val = val[0] if val else 1
            val = int(val)
            if 1 <= val <= 8:
                return val
    except _PARSE_ERRORS:
        pass
    return 1


def read_gps(path):
    """Extract GPS + basic metadata from an image. Returns dict or None."""
    try:
        block = _find_exif_block(path)
    except Exception:
        return None
    if not block:
        return None
    try:
        r = _Reader(block)
        ifd0 = r.read_ifd(r.first_ifd)
    except Exception:
        return None

    result = {}

    # Camera make / model (IFD0 tags 0x010F / 0x0110)
    for tag, key in ((0x010F, "make"), (0x0110, "model")):
        if tag in ifd0:
            ftype, count, raw = ifd0[tag]
            val = r.decode(ftype, count, raw)
            if isinstance(val, str) and val.strip():
                result[key] = val.strip()

    # DateTimeOriginal via EXIF sub-IFD (tag 0x8769 -> tag 0x9003)
    if 0x8769 in ifd0:
        try:
            exif_off = r.decode(*ifd0[0x8769])
            exif_ifd = r.read_ifd(int(exif_off))
            if 0x9003 in exif_ifd:
                dt = r.decode(*exif_ifd[0x9003])
                if isinstance(dt, str) and len(dt) >= 19:
                    # EXIF format 'YYYY:MM:DD HH:MM:SS' -> 'YYYY-MM-DD HH:MM:SS'
                    result["datetime"] = dt[:10].replace(":", "-") + dt[10:]
        except _PARSE_ERRORS:
            pass

    # GPS IFD (tag 0x8825)
    if 0x8825 not in ifd0:
        return result or None
    try:
        gps_off = int(r.decode(*ifd0[0x8825]))
        gps = r.read_ifd(gps_off)
    except Exception:
        return result or None

    parsed = {}
    for tag, name in _GPS_TAGS.items():
        if tag in gps:
            parsed[name] = r.decode(*gps[tag])

    lat = parsed.get("lat")
    lon = parsed.get("lon")
    if lat is not None and lon is not None:
        lat_list = lat if isinstance(lat, list) else [lat]
        lon_list = lon if isinstance(lon, list) else [lon]
        dlat = _dms_to_deg(lat_list, parsed.get("lat_ref"))
        dlon = _dms_to_deg(lon_list, parsed.get("lon_ref"))
        if dlat is not None and dlon is not None and (dlat != 0 or dlon != 0):
            result["lat"] = dlat
            result["lon"] = dlon

    if "alt" in parsed:
        try:
            alt = float(parsed["alt"])
            if parsed.get("alt_ref") in (1, b"\x01"):
                alt = -alt
            result["altitude"] = alt
        except _PARSE_ERRORS:
            pass

    if "direction" in parsed:
        try:
            result["direction"] = float(parsed["direction"])
        except _PARSE_ERRORS:
            pass

    # Only meaningful if we actually got a coordinate
    if "lat" not in result or "lon" not in result:
        return None
    return result
