"""Pure checks on uploaded hotel photographs: the type comes from the bytes (never the file name or the client's header), and the
pixel size from the image header. Only JPEG, PNG and WebP are accepted. No database access here."""

import struct

MAX_FULL_BYTES = 4 * 1024 * 1024
MAX_THUMB_BYTES = 256 * 1024
MAX_SIDE = 4096
MIN_SIDE = 64
MAX_THUMB_SIDE = 640


def sniff(data):
    """'image/jpeg' | 'image/png' | 'image/webp' | None from magic bytes."""
    if not data or len(data) < 16:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _png_size(data):
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _jpeg_size(data):
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        length = struct.unpack(">H", data[index + 2:index + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[index + 5:index + 9])
            return width, height
        index += 2 + length
    return None


def _webp_size(data):
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(data) >= 30:
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def image_size(data, mime):
    return {"image/png": _png_size, "image/jpeg": _jpeg_size, "image/webp": _webp_size}.get(mime, lambda _data: None)(data)


def check(data, thumb=False):
    """(info, '') with info {mime, width, height, bytes}, or (None, reason)."""
    limit = MAX_THUMB_BYTES if thumb else MAX_FULL_BYTES
    if not data:
        return None, "empty file"
    if len(data) > limit:
        return None, "file is larger than " + str(limit // 1024) + " KB"
    mime = sniff(data)
    if not mime:
        return None, "only JPEG, PNG or WebP photographs are accepted"
    size = image_size(data, mime)
    if not size:
        return None, "the image header could not be read"
    width, height = size
    top = MAX_THUMB_SIDE if thumb else MAX_SIDE
    if not (MIN_SIDE <= width <= top and MIN_SIDE <= height <= top):
        return None, "image must be " + str(MIN_SIDE) + ".." + str(top) + " pixels on each side"
    return {"mime": mime, "width": width, "height": height, "bytes": len(data)}, ""
