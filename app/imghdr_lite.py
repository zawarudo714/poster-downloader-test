"""
Zero-dependency image dimension reader.

Reads only the file header (~30 bytes for most formats) so it's fast and avoids
pulling Pillow into the project. Returns (width, height) in pixels, or None
if the format isn't recognised or the data is too short / malformed.

Supported: PNG, JPEG (baseline + progressive), WEBP (VP8/VP8L/VP8X), GIF87a/GIF89a.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional, Tuple


def read_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    if not data or len(data) < 12:
        return None

    # PNG: 8-byte sig + IHDR chunk; width @ offset 16, height @ offset 20 (big-endian)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) >= 24:
            try:
                w, h = struct.unpack(">II", data[16:24])
                return (w, h)
            except struct.error:
                return None
        return None

    # JPEG: walk segments looking for SOFn (Start Of Frame).
    if data[:2] == b"\xff\xd8":
        i = 2
        n = len(data)
        while i < n - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            # Skip fill bytes 0xFF
            while i < n and data[i] == 0xFF:
                i += 1
            if i >= n:
                return None
            marker = data[i]
            i += 1
            # Standalone markers without segment length: SOI, EOI, RSTn (not in stream here)
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            if i + 2 > n:
                return None
            seg_len = struct.unpack(">H", data[i:i+2])[0]
            # SOFn frames carry dimensions; SOF0 (0xC0) is baseline, SOF2 (0xC2) progressive,
            # 0xC4 (DHT), 0xC8 (JPG), 0xCC (DAC) are NOT frame markers — skip them.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                if i + seg_len <= n and seg_len >= 7:
                    h = struct.unpack(">H", data[i+3:i+5])[0]
                    w = struct.unpack(">H", data[i+5:i+7])[0]
                    return (w, h)
            i += seg_len
        return None

    # WEBP: 'RIFF' + 4 bytes filesize + 'WEBP' + chunk header at offset 12
    if data[:4] == b"RIFF" and len(data) >= 16 and data[8:12] == b"WEBP":
        chunk = data[12:16]
        try:
            if chunk == b"VP8 ":
                # VP8 lossy: width/height are 14-bit values starting at offset 26.
                if len(data) >= 30:
                    w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
                    h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
                    return (w, h)
            elif chunk == b"VP8L":
                # VP8L lossless: 1 byte signature 0x2F at 20, then 14-bit (width-1) and (height-1).
                if len(data) >= 25 and data[20] == 0x2F:
                    b1, b2, b3, b4 = data[21], data[22], data[23], data[24]
                    w = (((b2 & 0x3F) << 8) | b1) + 1
                    h = (((b4 & 0x0F) << 10) | (b3 << 2) | (b2 >> 6)) + 1
                    return (w, h)
            elif chunk == b"VP8X":
                # Extended: 24-bit canvas dims (each minus 1) at offsets 24..29.
                if len(data) >= 30:
                    w = (data[24] | (data[25] << 8) | (data[26] << 16)) + 1
                    h = (data[27] | (data[28] << 8) | (data[29] << 16)) + 1
                    return (w, h)
        except struct.error:
            return None
        return None

    # GIF: 'GIF87a' / 'GIF89a' + width/height as little-endian uint16 at offset 6.
    if data[:6] in (b"GIF87a", b"GIF89a"):
        if len(data) >= 10:
            try:
                w, h = struct.unpack("<HH", data[6:10])
                return (w, h)
            except struct.error:
                return None
        return None

    return None


def read_file_dimensions(path: Path) -> Optional[Tuple[int, int]]:
    """Read just the first 64 KiB — plenty for any image header."""
    try:
        with path.open("rb") as f:
            data = f.read(65536)
    except OSError:
        return None
    return read_dimensions(data)
