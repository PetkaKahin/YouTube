"""Isolated, resource-supervised WebVTT packaging; only server-created paths/JSON."""

import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import cast

from video.domain.encoding import mapping, rational
from video.infrastructure.subtitles import prepare_webvtt


def main(directory: Path) -> None:
    spec = mapping(json.loads((directory / "subtitle-spec.json").read_bytes()))
    package = prepare_webvtt(
        (directory / "normalized.vtt").read_bytes(),
        segment_durations=tuple(rational(value) for value in cast(list[object], spec["segments"])),
        source_origin=rational(spec["source_origin"]),
        presentation_origin=rational(spec["presentation_origin"]),
    )
    result: dict[str, object] = {"empty": package is None}
    if package is not None:
        (directory / "index.m3u8").write_text(package.playlist)
        elapsed = Fraction(0)
        for segment in package.segments:
            (directory / segment.name).write_bytes(segment.data)
            elapsed += segment.duration
            print(f"out_time_us={int(elapsed * 1000000)}", flush=True)
        result["target_duration"] = package.target_duration
        result["count"] = len(package.segments)
    (directory / "subtitle-result.json").write_text(json.dumps(result))
    print("progress=end", flush=True)


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]))
    except Exception:
        raise SystemExit("Subtitle package is invalid") from None
