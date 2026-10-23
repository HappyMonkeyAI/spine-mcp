"""Image handling: validate untrusted input, normalise to webp, pixel diffs, crops."""

from __future__ import annotations

import base64
import binascii
import io
from collections import deque
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
CELL = 16            # diff grid cell, px
CELL_CHANGED = 0.02  # fraction of a cell's pixels that must differ
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # refuse decompression bombs


class ImageError(ValueError):
    """The supplied image was missing, too large, or not an image."""


def load(path: str | None = None, data_base64: str | None = None) -> Image.Image:
    """Load and validate an agent-supplied image from a file path or base64."""
    if bool(path) == bool(data_base64):
        raise ImageError("give exactly one of image_path or image_base64")
    if path:
        file = Path(path).expanduser()
        if file.suffix.lower() not in IMAGE_SUFFIXES or not file.is_file():
            raise ImageError(f"image_path must be an existing .png/.jpg/.webp file: {path}")
        if file.stat().st_size > MAX_INPUT_BYTES:
            raise ImageError("image is larger than 20 MB")
        raw = file.read_bytes()
    else:
        try:
            raw = base64.b64decode(data_base64.split(",", 1)[-1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageError(f"image_base64 is not valid base64: {exc}") from exc
        if len(raw) > MAX_INPUT_BYTES:
            raise ImageError("image is larger than 20 MB")
    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise ImageError(f"not a readable image: {exc}") from exc
    if image.format not in {"PNG", "JPEG", "WEBP"}:
        raise ImageError(f"unsupported image format {image.format}")
    return image.convert("RGB")


def save_webp(image: Image.Image, target: Path) -> int:
    """Lossless: UI screenshots are flat colour, so lossless webp is smaller than lossy
    (ZeroGTerm 1280x800: 14 KB vs 16 KB) and diffs carry no compression noise."""
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "WEBP", lossless=True, quality=100, method=4)
    return target.stat().st_size


DEFAULT_THRESHOLD = 0.02


def diff(a: Image.Image, b: Image.Image, threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """Pixel difference between two captures: % changed and changed regions as bboxes.

    `threshold` (0-1) is the per-channel difference a pixel needs to count as changed.
    It is low by default because dark UIs change in small steps (ZeroGTerm's
    #0d1015 → #151a22 is 8/255) and captures are stored losslessly, so there is no
    compression noise to filter out.
    """
    note = None
    if a.size != b.size:
        note = f"sizes differ ({a.size[0]}x{a.size[1]} vs {b.size[0]}x{b.size[1]}); b resized to a for comparison"
        b = b.resize(a.size)
    delta = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    cutoff = max(1, round(threshold * 255))
    mask = delta.point(lambda v: 255 if v >= cutoff else 0)
    width, height = mask.size
    changed_pixels = mask.histogram()[255]

    cols, rows = max(1, width // CELL), max(1, height // CELL)
    cells = mask.resize((cols, rows), Image.BOX)
    hot = {(x, y) for y in range(rows) for x in range(cols) if cells.getpixel((x, y)) > CELL_CHANGED * 255}

    regions = []
    seen: set[tuple[int, int]] = set()
    for start in sorted(hot, key=lambda c: (c[1], c[0])):
        if start in seen:
            continue
        queue, xs, ys = deque([start]), [], []
        seen.add(start)
        while queue:
            cx, cy = queue.popleft()
            xs.append(cx)
            ys.append(cy)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (cx + dx, cy + dy)
                    if n in hot and n not in seen:
                        seen.add(n)
                        queue.append(n)
        sx, sy = width / cols, height / rows
        regions.append([round(min(xs) * sx), round(min(ys) * sy),
                        round((max(xs) + 1) * sx - min(xs) * sx), round((max(ys) + 1) * sy - min(ys) * sy)])
    regions.sort(key=lambda r: -(r[2] * r[3]))
    return {
        "changed_pct": round(100 * changed_pixels / (width * height), 3),
        "regions": regions[:20],
        "region_count": len(regions),
        **({"note": note} if note else {}),
    }


def preview(path: Path, crop: list[int] | None = None, max_width: int = 768) -> bytes:
    """A small webp for an agent's context: optional crop [x, y, w, h], then downscale."""
    image = Image.open(path).convert("RGB")
    if crop:
        x, y, w, h = (int(v) for v in crop)
        image = image.crop((max(0, x), max(0, y), min(image.width, x + w), min(image.height, y + h)))
    if image.width > max_width:
        image = image.resize((max_width, max(1, round(image.height * max_width / image.width))))
    out = io.BytesIO()
    image.save(out, "WEBP", quality=70)
    return out.getvalue()
