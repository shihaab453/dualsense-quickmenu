# Regenerates assets/icon.ico, the .exe's icon, from icons.render_app_icon.
#
#   .venv\Scripts\python.exe tools\make_icon.py
#
# The result is committed, so this only needs re-running if the mark changes.
# Multiple sizes go into the one .ico because Windows picks a different one per
# context — 16px in Explorer's list view, 32px on the desktop, 256px for the
# taskbar and large-icon views — and letting Windows downscale a single 256px
# image instead produces a visibly mushy small icon.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from icons import render_app_icon

_SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    app = QApplication(sys.argv)  # Qt needs one before rendering any pixmap
    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets",
        "icon.ico",
    )
    ok = _write_ico(out_path, [render_app_icon(size) for size in _SIZES])
    print(f"{'wrote' if ok else 'FAILED to write'} {out_path}")
    return 0 if ok else 1


def _write_ico(path: str, pixmaps) -> bool:
    """Writes a multi-image .ico. Each entry is stored as a PNG, which the ICO
    format has allowed since Vista and Windows reads fine — that avoids
    hand-rolling BMP+mask encoding for the alpha channel."""
    import struct

    from PySide6.QtCore import QBuffer, QByteArray

    encoded = []
    for pixmap in pixmaps:
        # The QByteArray must be held in its own name: QBuffer keeps a raw
        # pointer to it, and passing QByteArray() inline lets Python free the
        # temporary while the buffer is still writing into it — which segfaults
        # rather than raising.
        store = QByteArray()
        buffer = QBuffer(store)
        buffer.open(QBuffer.WriteOnly)
        if not pixmap.save(buffer, "PNG"):
            return False
        buffer.close()
        encoded.append((pixmap.width(), pixmap.height(), bytes(store)))

    header = struct.pack("<HHH", 0, 1, len(encoded))
    offset = len(header) + 16 * len(encoded)
    entries, payload = b"", b""
    for width, height, data in encoded:
        entries += struct.pack(
            "<BBBBHHII",
            0 if width >= 256 else width,   # 0 means 256 in the ICO format
            0 if height >= 256 else height,
            0,  # palette size: 0 for PNG-encoded entries
            0,  # reserved
            1,  # color planes
            32,  # bits per pixel
            len(data),
            offset,
        )
        payload += data
        offset += len(data)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(header + entries + payload)
    return True


if __name__ == "__main__":
    sys.exit(main())
