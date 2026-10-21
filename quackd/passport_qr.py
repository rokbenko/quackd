"""A passport's QR sticker, written and read with OpenCV, which quackd already depends on.

The sticker holds the passport's URL followed by `#rp=` and the robot's short id
(docs/passport/discovery.md#qr-codes-and-nfc-tags). OpenCV draws one pixel per module with two
modules of quiet zone already round the code, so the image is scaled by whole pixels, which keeps
every module square, and given two more modules of margin, the four a scanner needs.

OpenCV ships two detectors, and on codes its own encoder writes, the one built on its ArUco
machinery reads every one where the older one misses some, so it is asked first. Every sticker
is read back before it is saved, so quackd never prints a sticker its own `meet` cannot read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from quackd.passport.errors import PassportError

#: Pixels per module in the PNG, enough for a phone to read a sticker a few centimetres wide.
SCALE = 10
#: The quiet zone a scanner needs round a code, in modules, and what OpenCV draws of it already.
QUIET_ZONE = 4
OPENCV_BORDER = 2


def _decode(image: Any) -> str:
    import cv2

    for detector in (cv2.QRCodeDetectorAruco(), cv2.QRCodeDetector()):
        text, _points, _straight = detector.detectAndDecode(image)
        if text:
            return str(text)
    return ""


def write_qr(text: str, path: Path, *, scale: int = SCALE) -> tuple[int, int]:
    """`text` as a QR code PNG at `path`; returns its size in modules and in pixels.

    Error correction is level M, the usual choice for a printed label, and level L if a code
    at M will not read back."""
    import cv2

    for level in (cv2.QRCodeEncoder_CORRECT_LEVEL_M, cv2.QRCodeEncoder_CORRECT_LEVEL_L):
        params = cv2.QRCodeEncoder.Params()
        params.correction_level = level
        code = cv2.QRCodeEncoder.create(params).encode(text)
        if code is None or code.size == 0:
            raise PassportError("5.2", f"{len(text)} characters are more than one QR code holds")
        image = cv2.resize(code, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        margin = (QUIET_ZONE - OPENCV_BORDER) * scale
        image = cv2.copyMakeBorder(
            image, margin, margin, margin, margin, cv2.BORDER_CONSTANT, value=255
        )
        if _decode(image) == text:
            if not cv2.imwrite(str(path), image):
                raise OSError(f"could not write {path}")
            return int(code.shape[0]) - 2 * OPENCV_BORDER, int(image.shape[0])
    raise PassportError("5.2", "the QR code would not read back, so no sticker was written")


def read_qr(path: Path) -> str:
    """The text of the QR code in the picture at `path`."""
    import cv2

    image = cv2.imread(str(path))
    if image is None:
        raise PassportError("5.2", f"{path} is not a picture quackd can read")
    text = _decode(image)
    if not text:
        raise PassportError("5.2", f"no QR code could be read in {path}")
    return text
