"""Conservative HWP content identity; unsupported files keep their byte identity.

Hancom HWP 5.0: preserve body, styles, numbering, embedded data and unknown
streams. Ignore preview/save metadata and only the saved cursor in properties.
"""
import hashlib
import io
from pathlib import Path
import struct
import zlib

MAX_MATERIAL_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024


def _unpack(raw, compressed):
    if not compressed:
        if len(raw) > MAX_EXPANDED_BYTES:
            raise ValueError('Oversized HWP stream')
        return raw
    reader = zlib.decompressobj(-15)
    result = reader.decompress(raw, MAX_EXPANDED_BYTES + 1)
    if len(result) > MAX_EXPANDED_BYTES or not reader.eof:
        raise ValueError('Incomplete or oversized compressed HWP stream')
    if reader.unused_data:
        if len(reader.unused_data) != 8:
            raise ValueError('Unknown compressed-stream trailer')
        crc, length = struct.unpack('<II', reader.unused_data)
        if crc != zlib.crc32(result) & 0xffffffff or length != len(result):
            raise ValueError('Corrupt compressed-stream trailer')
    return result


def _docinfo(raw):
    result = bytearray()
    position = 0
    while position < len(raw):
        if position + 4 > len(raw):
            raise ValueError('Truncated HWP record')
        header = struct.unpack_from('<I', raw, position)[0]
        position += 4
        tag, level, size = header & 1023, (header >> 10) & 1023, header >> 20
        if size == 4095:
            if position + 4 > len(raw):
                raise ValueError('Truncated HWP length')
            size = struct.unpack_from('<I', raw, position)[0]
            position += 4
        if position + size > len(raw):
            raise ValueError('Truncated HWP payload')
        payload = raw[position:position + size]
        position += size
        if tag == 16:
            if size != 26:
                raise ValueError('Unknown document properties')
            payload = payload[:14]  # Keep section/start numbers; ignore saved cursor.
        elif tag == 27:
            continue  # Document-specific non-rendering user metadata.
        result.extend(struct.pack('<HHI', tag, level, len(payload)))
        result.extend(payload)
    return bytes(result)


def hwp_material_digest(data):
    if not data or len(data) > MAX_MATERIAL_BYTES or data[:8] != bytes.fromhex('d0cf11e0a1b11ae1'):
        return None
    try:
        import olefile
        with olefile.OleFileIO(io.BytesIO(data)) as document:
            if document.get_size('FileHeader') != 256:
                return None
            header = document.openstream('FileHeader').read()
            if not header.startswith(b'HWP Document File') or header[35] != 5:
                return None
            flags = struct.unpack_from('<I', header, 36)[0]
            if flags & 6:  # Encrypted/distribution documents are not deduplicated.
                return None
            parts = [('format', header[34:36] + struct.pack('<I', flags & ~1))]
            sections = 0
            total = 0
            for path in sorted(document.listdir()):
                name = '/'.join(path)
                if name in ('FileHeader', 'PrvText', 'PrvImage', '\x05HwpSummaryInformation', 'DocOptions/_LinkDoc'):
                    continue
                if document.get_size(path) > MAX_EXPANDED_BYTES:
                    return None
                raw = document.openstream(path).read()
                if name == 'DocInfo':
                    raw = _docinfo(_unpack(raw, flags & 1))
                elif len(path) == 2 and path[0] == 'BodyText' and path[1].startswith('Section'):
                    raw = _unpack(raw, flags & 1)
                    sections += 1
                total += len(raw)
                if total > MAX_EXPANDED_BYTES:
                    return None
                parts.append((name, raw))
            if not sections or not any(name == 'DocInfo' for name, raw in parts):
                return None
        digest = hashlib.sha256()
        for name, raw in parts:
            encoded = name.encode('utf8')
            digest.update(struct.pack('<II', len(encoded), len(raw)))
            digest.update(encoded)
            digest.update(raw)
        return 'hwp-material-v1:' + digest.hexdigest()
    except Exception:  # An unrecognized document is retained, never treated as equal.
        return None


def file_material_digest(path):
    path = Path(path)
    if path.suffix.lower() != '.hwp' or path.stat().st_size > MAX_MATERIAL_BYTES:
        return None
    return hwp_material_digest(path.read_bytes())
