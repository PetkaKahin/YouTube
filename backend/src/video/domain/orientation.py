"""Display geometry independent of FFmpeg's cropping autorotation filter."""

import re
from dataclasses import dataclass
from fractions import Fraction
from math import ceil, cos, isfinite, pi, sin, sqrt
from typing import cast


def stretch(matrix: tuple[Fraction, ...]) -> float:
    """Largest singular value: no source direction may be enlarged."""
    a, b, c, d = map(float, matrix)
    trace = a * a + b * b + c * c + d * d
    determinant = a * d - b * c
    return sqrt((trace + sqrt(max(0.0, trace * trace - 4 * determinant**2))) / 2)


@dataclass(frozen=True)
class AffineOrientation:
    width: int
    height: int
    matrix: tuple[Fraction, ...]

    @property
    def bounds(self) -> tuple[int, int]:
        a, b, c, d = self.matrix
        # One black pixel on each edge also contains the interpolation footprint.
        return (
            ceil(abs(a) * self.width + abs(b) * self.height) + 2,
            ceil(abs(c) * self.width + abs(d) * self.height) + 2,
        )

    @property
    def spec(self) -> dict[str, object]:
        return {
            "profile": "affine-v1",
            "width": self.width,
            "height": self.height,
            "matrix": [[item.numerator, item.denominator] for item in self.matrix],
        }

    @classmethod
    def parse(cls, value: object) -> AffineOrientation:
        if not isinstance(value, dict):
            raise ValueError
        spec = cast(dict[str, object], value)
        width, height, coefficients = spec.get("width"), spec.get("height"), spec.get("matrix")
        if (
            spec.get("profile") != "affine-v1"
            or type(width) is not int
            or type(height) is not int
            or min(width, height) < 1
            or not isinstance(coefficients, list)
            or len(cast(list[object], coefficients)) != 4
        ):
            raise ValueError
        matrix: list[Fraction] = []
        for item in cast(list[object], coefficients):
            if not isinstance(item, list):
                raise ValueError
            pair = cast(list[object], item)
            if len(pair) != 2 or type(pair[0]) is not int or type(pair[1]) is not int:
                raise ValueError
            matrix.append(Fraction(pair[0], pair[1]))
        result = cls(width, height, tuple(matrix))
        a, b, c, d = result.matrix
        scale = stretch(result.matrix)
        # Only a floating point arithmetic tolerance, never a media upscale allowance.
        if a * d == b * c or not isfinite(scale) or scale > 1 + 1e-12:
            raise ValueError
        return result


@dataclass(frozen=True)
class DisplayGeometry:
    width: int
    height: int
    sar: Fraction
    orientation: AffineOrientation | None = None


def display_geometry(
    width: int,
    height: int,
    sar: Fraction,
    rotation: Fraction,
    displaymatrix: object = None,
) -> DisplayGeometry:
    if min(width, height) < 1 or sar <= 0:
        raise ValueError
    matrix: tuple[Fraction, ...] | None = None
    if displaymatrix is not None:
        if not isinstance(displaymatrix, str):
            raise ValueError
        match = re.fullmatch(
            r"\s*00000000:\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+"
            r"00000001:\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+"
            r"00000002:\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*",
            displaymatrix,
        )
        if match is None:
            raise ValueError
        values = [int(item) for item in match.groups()]
        if any(not -(2**31) <= item < 2**31 for item in values):
            raise ValueError
        if values[2] or values[5] or not values[8]:
            raise ValueError
        unit = Fraction(2**14, values[8])
        # FFmpeg's row-vector display matrix becomes a column-vector pixel transform.
        a, b, c, d = matrix = tuple(values[index] * unit for index in (0, 3, 1, 4))
        if a * d == b * c:
            raise ValueError
        ordinary = a * d - b * c == 1 and {abs(item) for item in matrix} == {0, 1}
    else:
        ordinary = True
    if ordinary and rotation % 90 == 0:
        # Keep the existing geometry and cache identities for ordinary cardinal rotations.
        if rotation % 180:
            return DisplayGeometry(height, width, 1 / sar)
        return DisplayGeometry(width, height, sar)
    if matrix is None:
        angle = -float(rotation % 360) * pi / 180
        matrix = tuple(map(Fraction, (cos(angle), -sin(angle), sin(angle), cos(angle))))
    a, b, c, d = matrix
    matrix = (a * sar, b, c * sar, d)
    divisor = max(1.0, stretch(matrix))
    matrix = tuple(item / Fraction(divisor) for item in matrix)
    orientation = AffineOrientation.parse(AffineOrientation(width, height, matrix).spec)
    bound_w, bound_h = orientation.bounds
    return DisplayGeometry(bound_w, bound_h, Fraction(1), orientation)
