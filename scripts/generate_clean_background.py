from __future__ import annotations

import argparse
import math
from pathlib import Path
import struct
import zlib


PALETTE = [
    (249, 251, 245),
    (238, 248, 247),
    (232, 244, 255),
    (243, 240, 255),
    (255, 249, 238),
]


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(hi, max(lo, value))


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[float, float, float]:
    return (
        a[0] * (1.0 - t) + b[0] * t,
        a[1] * (1.0 - t) + b[1] * t,
        a[2] * (1.0 - t) + b[2] * t,
    )


def _palette_at(t: float) -> tuple[float, float, float]:
    t = _clamp(t)
    scaled = t * (len(PALETTE) - 1)
    idx = min(len(PALETTE) - 2, int(scaled))
    return _mix(PALETTE[idx], PALETTE[idx + 1], scaled - idx)


def _blend(base: tuple[float, float, float], target: tuple[int, int, int], amount: float) -> tuple[float, float, float]:
    amount = _clamp(amount)
    return (
        base[0] * (1.0 - amount) + target[0] * amount,
        base[1] * (1.0 - amount) + target[1] * amount,
        base[2] * (1.0 - amount) + target[2] * amount,
    )


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _write_png(path: Path, width: int, height: int, rgb: bytes) -> None:
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        start = y * stride
        raw.extend(rgb[start : start + stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", ihdr),
            _png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9)),
            _png_chunk(b"IEND", b""),
        ]
    )
    path.write_bytes(payload)


def _gradient(width: int, height: int) -> bytes:
    pixels = bytearray(width * height * 3)
    x_values = [x / max(1, width - 1) for x in range(width)]
    sin_a = [math.sin((x + 0.12) * math.pi) for x in x_values]
    sin_b = [math.sin((x + 0.54) * math.pi) for x in x_values]
    right_falloff = [((x - 0.86) ** 2) / 0.12 for x in x_values]

    pos = 0
    for yy in range(height):
        y = yy / max(1, height - 1)
        for idx, x in enumerate(x_values):
            color = _palette_at(0.68 * x + 0.42 * y)

            # Large, soft color washes only. No dots, stars, grid, or linework.
            wash_a = math.exp(-((y - (0.20 + 0.08 * sin_a[idx])) ** 2) / 0.050)
            wash_b = math.exp(-((y - (0.72 + 0.06 * sin_b[idx])) ** 2) / 0.070)
            wash_c = math.exp(-(right_falloff[idx] + ((y - 0.18) ** 2) / 0.18))

            color = _blend(color, (255, 255, 255), 0.050 * wash_a)
            color = _blend(color, (226, 243, 250), 0.045 * wash_b)
            color = _blend(color, (245, 240, 255), 0.040 * wash_c)

            pixels[pos] = round(color[0])
            pixels[pos + 1] = round(color[1])
            pixels[pos + 2] = round(color[2])
            pos += 3
    return bytes(pixels)


def generate(out: Path, width: int, height: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_png(out, width, height, _gradient(width, height))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a clean pastel briefing background.")
    parser.add_argument("--out", type=Path, default=Path("assets/backgrounds/starfield-clean-pure-v1.png"))
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args()
    generate(args.out, args.width, args.height)
    print(args.out.resolve())


if __name__ == "__main__":
    main()
