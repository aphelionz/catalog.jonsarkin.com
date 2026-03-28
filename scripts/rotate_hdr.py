#!/usr/bin/env python3
"""
Rotate an Apple MPF JPEG (HDR or Portrait mode) while preserving the embedded secondary image.

Usage:
    python3 rotate_hdr.py <degrees> <input.jpeg> <output.jpeg>
    degrees: 90, 180, 270
"""
import sys
import struct
import subprocess
import os
import tempfile


def find_gain_map_start(data: bytes) -> int:
    """Find the embedded secondary image offset using MPF APP2."""
    pos = 0
    while pos < min(len(data), 65536):
        if data[pos:pos + 2] == b'\xff\xe2':
            seg_len = struct.unpack('>H', data[pos + 2:pos + 4])[0]
            if data[pos + 4:pos + 8] == b'MPF\x00':
                mpf_app2_offset = pos
                mpf_data = data[pos + 8:pos + 2 + seg_len]

                bo = '<' if mpf_data[:2] == b'II' else '>'
                ifd_offset = struct.unpack(bo + 'I', mpf_data[4:8])[0]
                num_entries = struct.unpack(bo + 'H', mpf_data[ifd_offset:ifd_offset + 2])[0]

                for i in range(num_entries):
                    ep = ifd_offset + 2 + i * 12
                    tag = struct.unpack(bo + 'H', mpf_data[ep:ep + 2])[0]
                    if tag == 0xB002:
                        byte_count = struct.unpack(bo + 'I', mpf_data[ep + 4:ep + 8])[0]
                        entry_offset = struct.unpack(bo + 'I', mpf_data[ep + 8:ep + 12])[0]
                        num_images = byte_count // 16
                        last_ep = entry_offset + (num_images - 1) * 16
                        gm_offset = struct.unpack(bo + 'I', mpf_data[last_ep + 8:last_ep + 12])[0]
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
    return -1


def patch_mpf_offset(primary_data: bytes, gainmap_size: int) -> bytes:
    """Update MPF entries in the rotated primary: img[0].size, img[1].offset, img[1].size."""
    data = bytearray(primary_data)
    pos = 0
    while pos < min(len(data), 65536):
        if data[pos:pos + 2] == b'\xff\xe2':
            seg_len = struct.unpack('>H', bytes(data[pos + 2:pos + 4]))[0]
            if data[pos + 4:pos + 8] == b'MPF\x00':
                mpf_tiff_start = pos + 8
                mpf_data = bytes(data[mpf_tiff_start:pos + 2 + seg_len])

                bo = '<' if mpf_data[:2] == b'II' else '>'
                ifd_offset = struct.unpack(bo + 'I', mpf_data[4:8])[0]
                num_entries = struct.unpack(bo + 'H', mpf_data[ifd_offset:ifd_offset + 2])[0]

                for i in range(num_entries):
                    ep = ifd_offset + 2 + i * 12
                    tag = struct.unpack(bo + 'H', mpf_data[ep:ep + 2])[0]
                    if tag == 0xB002:
                        byte_count = struct.unpack(bo + 'I', mpf_data[ep + 4:ep + 8])[0]
                        entry_offset = struct.unpack(bo + 'I', mpf_data[ep + 8:ep + 12])[0]
                        num_images = byte_count // 16

                        # Patch img[0].size = actual size of rotated primary JPEG
                        img0_size_pos = mpf_tiff_start + entry_offset + 4
                        data[img0_size_pos:img0_size_pos + 4] = struct.pack(bo + 'I', len(primary_data))

                        # Patch img[last].offset = distance from tiff_start to gain map
                        last_ep = entry_offset + (num_images - 1) * 16
                        new_offset = len(primary_data) - mpf_tiff_start
                        offset_field_pos = mpf_tiff_start + last_ep + 8
                        data[offset_field_pos:offset_field_pos + 4] = struct.pack(bo + 'I', new_offset)

                        # Patch img[last].size = actual size of rotated gain map
                        size_field_pos = mpf_tiff_start + last_ep + 4
                        data[size_field_pos:size_field_pos + 4] = struct.pack(bo + 'I', gainmap_size)

                        return bytes(data)
                break
            pos += 2 + seg_len
        elif data[pos] == 0xFF:
            if data[pos + 1] in (0xD8, 0xD9, 0x01) or 0xD0 <= data[pos + 1] <= 0xD7:
                pos += 2
            elif pos + 3 < len(data):
                seg_len = struct.unpack('>H', bytes(data[pos + 2:pos + 4]))[0]
                pos += 2 + seg_len
            else:
                pos += 1
        else:
            pos += 1
    return bytes(data)


def reset_exif_orientation(data: bytes) -> bytes:
    """Set EXIF Orientation tag to 1 (TopLeft/normal) if present."""
    data = bytearray(data)
    pos = 2  # skip SOI
    while pos < len(data) - 3:
        if data[pos] != 0xFF:
            break
        seg_len = struct.unpack('>H', bytes(data[pos + 2:pos + 4]))[0]
        if data[pos:pos + 2] == b'\xff\xe1' and data[pos + 4:pos + 10] == b'Exif\x00\x00':
            tiff_start = pos + 10
            tiff = bytes(data[tiff_start:pos + 2 + seg_len])
            if len(tiff) < 8:
                break
            bo = '<' if tiff[:2] == b'II' else '>'
            ifd_offset = struct.unpack(bo + 'I', tiff[4:8])[0]
            if ifd_offset + 2 > len(tiff):
                break
            num_entries = struct.unpack(bo + 'H', tiff[ifd_offset:ifd_offset + 2])[0]
            for i in range(num_entries):
                ep = ifd_offset + 2 + i * 12
                if ep + 12 > len(tiff):
                    break
                if struct.unpack(bo + 'H', tiff[ep:ep + 2])[0] == 0x0112:  # Orientation
                    abs_pos = tiff_start + ep + 8
                    data[abs_pos:abs_pos + 2] = struct.pack(bo + 'H', 1)
                    return bytes(data)
            break
        pos += 2 + seg_len
    return bytes(data)


def rotate_jpeg(input_path: str, output_path: str, degrees: int):
    subprocess.run(
        ['jpegtran', '-rotate', str(degrees), '-copy', 'all',
         '-outfile', output_path, input_path],
        check=True
    )
    with open(output_path, 'rb') as f:
        data = f.read()
    data = reset_exif_orientation(data)
    with open(output_path, 'wb') as f:
        f.write(data)


def main():
    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} <degrees> <input.jpeg> <output.jpeg>')
        print(f'       {sys.argv[0]} fix-orientation <file.jpeg>')
        sys.exit(1)

    if sys.argv[1] == 'fix-orientation':
        if len(sys.argv) != 3:
            print(f'Usage: {sys.argv[0]} fix-orientation <file.jpeg>')
            sys.exit(1)
        path = sys.argv[2]
        with open(path, 'rb') as f:
            data = f.read()
        data = reset_exif_orientation(data)
        with open(path, 'wb') as f:
            f.write(data)
        print(f'Orientation reset: {path}')
        return

    if len(sys.argv) != 4:
        print(f'Usage: {sys.argv[0]} <degrees> <input.jpeg> <output.jpeg>')
        sys.exit(1)

    degrees = int(sys.argv[1])
    input_path = sys.argv[2]
    output_path = sys.argv[3]

    with open(input_path, 'rb') as f:
        data = f.read()

    gm_start = find_gain_map_start(data)

    if gm_start < 0:
        # No embedded secondary image — plain jpegtran rotation
        rotate_jpeg(input_path, output_path, degrees)
        print(f'No MPF secondary image found; plain rotation applied.')
        return

    primary_data = data[:gm_start]
    gainmap_data = data[gm_start:]
    print(f'MPF secondary image found at offset {gm_start} ({len(gainmap_data)} bytes)')

    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
        f.write(primary_data)
        primary_tmp = f.name
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
        f.write(gainmap_data)
        gainmap_tmp = f.name

    rotated_primary_tmp = primary_tmp + '.rot.jpg'
    rotated_gainmap_tmp = gainmap_tmp + '.rot.jpg'

    try:
        rotate_jpeg(primary_tmp, rotated_primary_tmp, degrees)
        rotate_jpeg(gainmap_tmp, rotated_gainmap_tmp, degrees)

        with open(rotated_primary_tmp, 'rb') as f:
            rot_primary = f.read()
        with open(rotated_gainmap_tmp, 'rb') as f:
            rot_gainmap = f.read()

        # Patch the MPF offset in the rotated primary
        rot_primary_patched = patch_mpf_offset(rot_primary, len(rot_gainmap))

        with open(output_path, 'wb') as f:
            f.write(rot_primary_patched)
            f.write(rot_gainmap)

        print(f'Done: {len(rot_primary_patched) + len(rot_gainmap)} bytes '
              f'(primary: {len(rot_primary_patched)}, secondary: {len(rot_gainmap)})')
    finally:
        for p in [primary_tmp, gainmap_tmp, rotated_primary_tmp, rotated_gainmap_tmp]:
            if os.path.exists(p):
                os.unlink(p)


if __name__ == '__main__':
    main()
