"""Numeric-only, full-canvas affine rendering shared by video and thumbnail decoders."""

from fractions import Fraction

from video.domain.orientation import AffineOrientation


def input_orientation(value: object, index: int) -> list[str]:
    if value is None:
        return []
    # Neutralize input metadata as well as autorotation: output must not rotate twice.
    AffineOrientation.parse(value)
    return ["-noautorotate", f"-display_rotation:{index}", "0"]


def orientation_filter(value: object) -> str:
    if value is None:
        return ""
    orientation = AffineOrientation.parse(value)
    width, height = orientation.bounds
    # A black border prevents perspective's edge sampler from extending image pixels.
    canvas_w, canvas_h = max(width, orientation.width + 4), max(height, orientation.height + 4)
    canvas_w += (canvas_w - orientation.width) % 2
    canvas_h += (canvas_h - orientation.height) % 2
    cx, cy = Fraction(canvas_w, 2), Fraction(canvas_h, 2)
    a, b, c, d = orientation.matrix
    corners: list[str] = []
    for index, (x, y) in enumerate(((0, 0), (canvas_w, 0), (0, canvas_h), (canvas_w, canvas_h))):
        dest_x, dest_y = cx + a * (x - cx) + b * (y - cy), cy + c * (x - cx) + d * (y - cy)
        corners.extend((f"x{index}={dest_x}", f"y{index}={dest_y}"))
    return (
        "format=yuv444p16le,"
        f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"perspective=sense=destination:{':'.join(corners)},"
        f"crop={width}:{height}:(iw-ow)/2:(ih-oh)/2,setsar=1,"
    )
