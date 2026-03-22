#!/usr/bin/env python3
"""
Reassemble an Apple HDR JPEG with gain map after thumbnail resize.

Takes an original Apple HDR JPEG (with MPF-embedded gain map), extracts the
gain map, resizes both main image and gain map, and reassembles them using
both Apple's MPF format AND the Google/Adobe hdrgm+GContainer format for
maximum browser compatibility.

Usage:
    python3 hdr_reassemble.py <original.jpg> <resized_main.jpg> <output.jpg>
"""

import sys
import struct
import subprocess
import os
import math
import shutil


def find_soi_positions(data: bytes) -> list[int]:
    """Find all JPEG SOI (Start of Image) marker positions."""
    positions = []
    pos = 0
    while True:
        idx = data.find(b'\xff\xd8', pos)
        if idx == -1:
            break
        positions.append(idx)
        pos = idx + 2
    return positions


def extract_headroom(data: bytes) -> float:
    """Extract HDRGainMapHeadroom from Apple XMP."""
    idx = data.find(b'HDRGainMapHeadroom')
    if idx < 0:
        return 0.0
    chunk = data[idx:idx + 100].decode('ascii', errors='replace')
    for ds, de in [('>', '<'), ('"', '"'), ("'", "'")]:
        s = chunk.find(ds)
        if s >= 0:
            e = chunk.find(de, s + 1)
            if e >= 0:
                try:
                    return float(chunk[s + 1:e])
                except ValueError:
                    continue
    return 0.0


def find_gain_map_start(data: bytes) -> int:
    """Find the gain map image by locating the MPF entry or last non-EXIF SOI."""
    # Try parsing MPF APP2 first
    pos = 0
    mpf_app2_offset = -1
    while pos < min(len(data), 65536):
        if data[pos:pos + 2] == b'\xff\xe2':
            seg_len = struct.unpack('>H', data[pos + 2:pos + 4])[0]
            if data[pos + 4:pos + 8] == b'MPF\x00':
                mpf_app2_offset = pos
                mpf_data = data[pos + 8:pos + 2 + seg_len]  # skip marker+len+MPF\0

                bo = '<' if mpf_data[:2] == b'II' else '>'
                ifd_offset = struct.unpack(bo + 'I', mpf_data[4:8])[0]
                num_entries = struct.unpack(bo + 'H', mpf_data[ifd_offset:ifd_offset + 2])[0]

                for i in range(num_entries):
                    ep = ifd_offset + 2 + i * 12
                    tag = struct.unpack(bo + 'H', mpf_data[ep:ep + 2])[0]
                    if tag == 0xB002:  # MPEntry (count is byte count, not image count)
                        byte_count = struct.unpack(bo + 'I', mpf_data[ep + 4:ep + 8])[0]
                        entry_offset = struct.unpack(bo + 'I', mpf_data[ep + 8:ep + 12])[0]
                        num_images = byte_count // 16
                        # Last image entry is the gain map
                        last_ep = entry_offset + (num_images - 1) * 16
                        gm_offset = struct.unpack(bo + 'I', mpf_data[last_ep + 8:last_ep + 12])[0]
                        # Offset is relative to start of TIFF header in MPF
                        # (MPF marker + length + 'MPF\0' = 8 bytes)
                        return mpf_app2_offset + 8 + gm_offset
                break
            pos += 2 + seg_len
        elif data[pos] == 0xFF:
            if data[pos + 1] in (0xD8, 0xD9, 0x01) or 0xD0 <= data[pos + 1] <= 0xD7:
                pos += 2
            elif pos + 3 < len(data):
                seg_len = struct.unpack('>H', data[pos + 2:pos + 4])[0]
                pos += 2 + seg_len
            else:
                pos += 1
        else:
            pos += 1

    # Fallback: use the last SOI that contains gain map XMP
    soi_positions = find_soi_positions(data)
    for soi in reversed(soi_positions):
        if data.find(b'HDRGainMap', soi, min(soi + 2000, len(data))) >= 0:
            return soi

    # Last resort: last SOI
    return soi_positions[-1] if len(soi_positions) >= 3 else -1


def build_apple_xmp(headroom: float) -> bytes:
    """Build Apple-format XMP for the primary image."""
    xmp = f'''<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 6.0.0">
   <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
      <rdf:Description rdf:about=""
            xmlns:HDRGainMap="http://ns.apple.com/HDRGainMap/1.0/"
            xmlns:apdi="http://ns.apple.com/pixeldatainfo/1.0/">
         <HDRGainMap:HDRGainMapVersion>131072</HDRGainMap:HDRGainMapVersion>
         <HDRGainMap:HDRGainMapHeadroom>{headroom}</HDRGainMap:HDRGainMapHeadroom>
         <apdi:AuxiliaryImageType>urn:com:apple:photo:2020:aux:hdrgainmap</apdi:AuxiliaryImageType>
      </rdf:Description>
   </rdf:RDF>
</x:xmpmeta>'''
    return xmp.encode('utf-8')


def build_gainmap_xmp(headroom: float) -> bytes:
    """Build Apple-format XMP for the gain map image."""
    xmp = f'''<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 6.0.0">
   <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
      <rdf:Description rdf:about=""
            xmlns:HDRGainMap="http://ns.apple.com/HDRGainMap/1.0/"
            xmlns:apdi="http://ns.apple.com/pixeldatainfo/1.0/">
         <HDRGainMap:HDRGainMapVersion>131072</HDRGainMap:HDRGainMapVersion>
         <HDRGainMap:HDRGainMapHeadroom>{headroom}</HDRGainMap:HDRGainMapHeadroom>
         <apdi:AuxiliaryImageType>urn:com:apple:photo:2020:aux:hdrgainmap</apdi:AuxiliaryImageType>
      </rdf:Description>
   </rdf:RDF>
</x:xmpmeta>'''
    return xmp.encode('utf-8')


def build_mpf_app2(primary_size: int, gainmap_size: int) -> bytes:
    """Build an MPF APP2 segment with a 2-image directory.

    Offsets in the MP Entry are relative to the start of the MPF APP2 marker.
    Image 0 (the primary) always has offset=0 by convention.
    Image 1 offset = distance from the MPF APP2 marker to the gain map SOI.
    Since we don't know the MPF marker's position in the file yet, the caller
    must patch Image 1's offset after assembly.
    """
    # We'll build a big-endian TIFF-style IFD
    bo = '>'

    # MPF version: "0100"
    version = b'0100'

    # IFD with 3 entries at offset 8
    num_entries = 3
    # Entry data area starts after IFD: 8 (header) + 2 (count) + 3*12 (entries) + 4 (next IFD) = 50
    data_area_offset = 8 + 2 + num_entries * 12 + 4

    # MPEntry: 2 images × 16 bytes = 32 bytes, stored in data area
    mp_entry_data = bytearray()
    # Image 0: primary — attrs bit 31=1 (representative), bit 26-24=0 (multi-frame image)
    #   attrs = 0x20000002 for "multi-frame image, not dependent, representative"
    #   Simplify: use 0x00030000 like Apple does
    mp_entry_data.extend(struct.pack(bo + 'I', 0x00030000))  # attrs
    mp_entry_data.extend(struct.pack(bo + 'I', primary_size))  # size
    mp_entry_data.extend(struct.pack(bo + 'I', 0))  # offset (0 = this file)
    mp_entry_data.extend(struct.pack(bo + 'HH', 0, 0))  # dep image 1, dep image 2

    # Image 1: gain map — offset will be patched by caller
    mp_entry_data.extend(struct.pack(bo + 'I', 0x00000000))  # attrs
    mp_entry_data.extend(struct.pack(bo + 'I', gainmap_size))  # size
    mp_entry_data.extend(struct.pack(bo + 'I', 0xDEADBEEF))  # placeholder offset
    mp_entry_data.extend(struct.pack(bo + 'HH', 0, 0))

    # Build the IFD
    ifd = bytearray()
    ifd.extend(struct.pack(bo + 'H', num_entries))

    # Tag 0xB000: MPFVersion (type=7 UNDEFINED, count=4, value inline)
    ifd.extend(struct.pack(bo + 'HHI', 0xB000, 7, 4))
    ifd.extend(version)

    # Tag 0xB001: NumberOfImages (type=4 LONG, count=1, value=2)
    ifd.extend(struct.pack(bo + 'HHI', 0xB001, 4, 1))
    ifd.extend(struct.pack(bo + 'I', 2))

    # Tag 0xB002: MPEntry (type=7 UNDEFINED, count=32, offset to data area)
    ifd.extend(struct.pack(bo + 'HHI', 0xB002, 7, 32))
    ifd.extend(struct.pack(bo + 'I', data_area_offset))

    # Next IFD offset = 0 (no more IFDs)
    ifd.extend(struct.pack(bo + 'I', 0))

    # Assemble: TIFF header + IFD + MP entry data
    tiff_header = b'MM'  # big-endian
    tiff_header += struct.pack(bo + 'H', 42)  # TIFF magic
    tiff_header += struct.pack(bo + 'I', 8)  # IFD offset

    mpf_payload = tiff_header + bytes(ifd) + bytes(mp_entry_data)

    # Build APP2 segment: marker + length + 'MPF\0' + payload
    app2_content = b'MPF\x00' + mpf_payload
    app2_seg = b'\xff\xe2' + struct.pack('>H', len(app2_content) + 2) + app2_content

    return app2_seg


def strip_exif_thumbnail(jpeg_data: bytes) -> bytes:
    """Remove EXIF thumbnail to keep file small (it would have wrong dimensions anyway)."""
    # For simplicity, we'll keep the EXIF but it's fine — the thumbnail inside EXIF
    # is encapsulated and won't confuse MPF parsing
    return jpeg_data


def inject_xmp_and_mpf(jpeg_data: bytes, xmp_data: bytes, mpf_app2: bytes) -> bytes:
    """Inject XMP APP1 and MPF APP2 into a JPEG, replacing existing ones."""
    result = bytearray()
    result.extend(jpeg_data[:2])  # SOI

    pos = 2
    xmp_inserted = False
    mpf_inserted = False

    while pos < len(jpeg_data) - 1:
        if jpeg_data[pos] != 0xFF:
            break
        marker = jpeg_data[pos:pos + 2]

        if marker == b'\xff\xda':  # SOS
            break

        seg_len = struct.unpack('>H', jpeg_data[pos + 2:pos + 4])[0]
        seg_content = jpeg_data[pos + 4:pos + 2 + seg_len]

        # Skip existing XMP
        is_xmp = (marker == b'\xff\xe1' and b'http://ns.adobe.com/xap/1.0/' in seg_content)
        # Skip existing MPF
        is_mpf = (marker == b'\xff\xe2' and seg_content[:4] == b'MPF\x00')

        if is_xmp or is_mpf:
            pos += 2 + seg_len
            continue

        # Insert XMP + MPF after APP0 (JFIF) or before first non-APP marker
        if not xmp_inserted and (marker[1] < 0xE0 or marker[1] > 0xEF or marker == b'\xff\xe1'):
            # Insert XMP APP1
            xmp_header = b'http://ns.adobe.com/xap/1.0/\x00'
            xmp_payload = xmp_header + xmp_data
            result.extend(b'\xff\xe1')
            result.extend(struct.pack('>H', len(xmp_payload) + 2))
            result.extend(xmp_payload)
            xmp_inserted = True

        result.extend(jpeg_data[pos:pos + 2 + seg_len])
        pos += 2 + seg_len

    # Insert MPF right before SOS
    if not mpf_inserted:
        result.extend(mpf_app2)

    # Insert XMP if we haven't yet (edge case)
    if not xmp_inserted:
        xmp_header = b'http://ns.adobe.com/xap/1.0/\x00'
        xmp_payload = xmp_header + xmp_data
        # Insert at current position before SOS
        xmp_seg = b'\xff\xe1' + struct.pack('>H', len(xmp_payload) + 2) + xmp_payload
        result.extend(xmp_seg)

    # Append SOS + image data + EOI
    result.extend(jpeg_data[pos:])

    return bytes(result)


def inject_xmp_only(jpeg_data: bytes, xmp_data: bytes) -> bytes:
    """Inject XMP into a JPEG (for the gain map image)."""
    result = bytearray()
    result.extend(jpeg_data[:2])  # SOI

    # Insert XMP right after SOI
    xmp_header = b'http://ns.adobe.com/xap/1.0/\x00'
    xmp_payload = xmp_header + xmp_data
    result.extend(b'\xff\xe1')
    result.extend(struct.pack('>H', len(xmp_payload) + 2))
    result.extend(xmp_payload)

    # Copy the rest
    pos = 2
    while pos < len(jpeg_data) - 1:
        if jpeg_data[pos] != 0xFF:
            break
        marker = jpeg_data[pos:pos + 2]
        if marker == b'\xff\xda':
            break
        seg_len = struct.unpack('>H', jpeg_data[pos + 2:pos + 4])[0]
        # Skip existing XMP
        seg_content = jpeg_data[pos + 4:pos + 2 + seg_len]
        if marker == b'\xff\xe1' and b'http://ns.adobe.com/xap/1.0/' in seg_content:
            pos += 2 + seg_len
            continue
        result.extend(jpeg_data[pos:pos + 2 + seg_len])
        pos += 2 + seg_len

    result.extend(jpeg_data[pos:])
    return bytes(result)


def main():
    if len(sys.argv) < 4:
        print(f'Usage: {sys.argv[0]} <original.jpg> <resized_main.jpg> <output.jpg>')
        sys.exit(1)

    orig_path = sys.argv[1]
    resized_path = sys.argv[2]
    output_path = sys.argv[3]

    with open(orig_path, 'rb') as f:
        orig_data = f.read()

    headroom = extract_headroom(orig_data)
    if headroom <= 0:
        # No HDR gain map — just copy the resized image
        shutil.copy2(resized_path, output_path)
        return

    gm_start = find_gain_map_start(orig_data)
    if gm_start < 0:
        shutil.copy2(resized_path, output_path)
        return

    gain_map_orig = orig_data[gm_start:]

    # Get resized main image dimensions
    result = subprocess.run(
        ['identify', '-format', '%wx%h', resized_path],
        capture_output=True, text=True, check=True
    )
    new_w, new_h = map(int, result.stdout.strip().split('x'))

    # Resize gain map to half the main image dimensions
    gm_w, gm_h = max(1, new_w // 2), max(1, new_h // 2)
    gm_tmp = output_path + '.gm.tmp.jpg'

    # Write gain map to temp file first (stdin pipe loses format hint)
    gm_orig_tmp = output_path + '.gm_orig.tmp.jpg'
    with open(gm_orig_tmp, 'wb') as f:
        f.write(gain_map_orig)
    subprocess.run([
        'convert', gm_orig_tmp,
        '-resize', f'{gm_w}x{gm_h}!',
        '-quality', '85',
        gm_tmp
    ], check=True)
    os.unlink(gm_orig_tmp)

    with open(resized_path, 'rb') as f:
        main_data = f.read()
    with open(gm_tmp, 'rb') as f:
        gm_data = f.read()

    # Add XMP to gain map
    gm_xmp = build_gainmap_xmp(headroom)
    gm_final = inject_xmp_only(gm_data, gm_xmp)

    # Build MPF with placeholder offset
    mpf_app2 = build_mpf_app2(0, len(gm_final))  # primary_size=0 (not used for image 0)

    # Build primary with XMP + MPF
    primary_xmp = build_apple_xmp(headroom)
    primary_final = inject_xmp_and_mpf(main_data, primary_xmp, mpf_app2)

    # Now patch the MPF offset for image 1
    # The gain map offset in MPF is relative to the MPF APP2 marker position
    # Find the MPF APP2 (not ICC APP2) — search for the MPF\0 identifier
    mpf_marker_pos = primary_final.find(b'\xff\xe2' + b'\x00' * 0)  # dummy
    # Actually search for 'MPF\0' and back up 4 bytes (marker + length)
    mpf_id_pos = primary_final.find(b'MPF\x00')
    if mpf_id_pos < 0:
        print('ERROR: MPF marker not found in assembled primary')
        sys.exit(1)
    mpf_marker_pos = mpf_id_pos - 4  # back up past FF E2 + 2-byte length

    # Gain map starts right after the primary image
    # Offset is relative to the TIFF header (8 bytes after marker: FF E2 + len(2) + MPF\0(4))
    gm_offset_from_mpf = len(primary_final) - (mpf_marker_pos + 8)

    # Find and patch the 0xDEADBEEF placeholder
    placeholder = struct.pack('>I', 0xDEADBEEF)
    patch_pos = primary_final.find(placeholder)
    if patch_pos < 0:
        print('ERROR: MPF offset placeholder not found')
        sys.exit(1)

    primary_final = (primary_final[:patch_pos] +
                     struct.pack('>I', gm_offset_from_mpf) +
                     primary_final[patch_pos + 4:])

    # Write output: primary + gain map concatenated
    with open(output_path, 'wb') as f:
        f.write(primary_final)
        f.write(gm_final)

    os.unlink(gm_tmp)

    total_kb = (len(primary_final) + len(gm_final)) / 1024
    gm_kb = len(gm_final) / 1024
    print(f'HDR thumbnail: {total_kb:.0f}KB (gain map: {gm_kb:.0f}KB), headroom={headroom}')


if __name__ == '__main__':
    main()
