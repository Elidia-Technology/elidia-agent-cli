"""Terminal image display — protocol detection and inline rendering.

Detects terminal capabilities and renders images inline using:
  - iTerm2 inline images (OSC 1337)
  - Kitty graphics protocol (ESC _G)
  - Sixel graphics
  - Fallback: save to file and print path
"""
from __future__ import annotations

import base64
import logging
import os
import struct
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class TerminalProtocol(str, Enum):
    ITERM2 = "iterm2"
    KITTY = "kitty"
    SIXEL = "sixel"
    NONE = "none"


@dataclass
class DisplayResult:
    protocol: TerminalProtocol
    displayed: bool
    file_path: str = ""
    message: str = ""


def detect_terminal() -> TerminalProtocol:
    logger.debug("Entered into detect_terminal")

    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    if term_program in ("iterm.app", "iterm2"):
        return TerminalProtocol.ITERM2

    if os.environ.get("KITTY_WINDOW_ID"):
        return TerminalProtocol.KITTY

    term = os.environ.get("TERM", "")
    if "xterm" in term:
        lc_terminal = os.environ.get("LC_TERMINAL", "").lower()
        if lc_terminal == "iterm2":
            return TerminalProtocol.ITERM2

    if _check_sixel_support():
        return TerminalProtocol.SIXEL

    return TerminalProtocol.NONE


def display_image(
    image_path: str | Path,
    max_width: int = 80,
    max_height: int = 24,
    protocol: TerminalProtocol | None = None,
) -> DisplayResult:
    logger.debug(f"Entered into display_image: path={image_path}")
    path = Path(image_path)
    if not path.exists():
        return DisplayResult(
            protocol=TerminalProtocol.NONE,
            displayed=False,
            file_path=str(path),
            message=f"File not found: {path}",
        )

    proto = protocol or detect_terminal()
    image_data = path.read_bytes()

    if proto == TerminalProtocol.ITERM2:
        return _display_iterm2(image_data, path, max_width)
    elif proto == TerminalProtocol.KITTY:
        return _display_kitty(image_data, path, max_width)
    elif proto == TerminalProtocol.SIXEL:
        return _display_sixel(image_data, path, max_width, max_height)
    else:
        return DisplayResult(
            protocol=TerminalProtocol.NONE,
            displayed=False,
            file_path=str(path),
            message=f"Image saved to: {path}",
        )


def _display_iterm2(data: bytes, path: Path, max_width: int) -> DisplayResult:
    logger.debug(f"Entered into _display_iterm2: size={len(data)}")
    b64 = base64.b64encode(data).decode("ascii")

    filename = base64.b64encode(path.name.encode()).decode("ascii")
    size = len(data)

    seq = (
        f"\033]1337;File=name={filename};size={size};"
        f"inline=1;width=auto;height=auto;preserveAspectRatio=1:"
        f"{b64}\a"
    )

    sys.stdout.write(seq)
    sys.stdout.write("\n")
    sys.stdout.flush()

    return DisplayResult(
        protocol=TerminalProtocol.ITERM2,
        displayed=True,
        file_path=str(path),
        message=f"Displayed via iTerm2 protocol ({len(data)} bytes)",
    )


def _display_kitty(data: bytes, path: Path, max_width: int) -> DisplayResult:
    logger.debug(f"Entered into _display_kitty: size={len(data)}")
    b64 = base64.b64encode(data).decode("ascii")

    chunk_size = 4096
    chunks = [b64[i:i + chunk_size] for i in range(0, len(b64), chunk_size)]

    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        more = 0 if is_last else 1

        if i == 0:
            header = f"a=T,f=100,m={more}"
        else:
            header = f"m={more}"

        sys.stdout.write(f"\033_G{header};{chunk}\033\\")

    sys.stdout.write("\n")
    sys.stdout.flush()

    return DisplayResult(
        protocol=TerminalProtocol.KITTY,
        displayed=True,
        file_path=str(path),
        message=f"Displayed via Kitty protocol ({len(data)} bytes)",
    )


def _display_sixel(data: bytes, path: Path, max_width: int, max_height: int) -> DisplayResult:
    logger.debug(f"Entered into _display_sixel: size={len(data)}")
    try:
        from PIL import Image
    except ImportError:
        return DisplayResult(
            protocol=TerminalProtocol.SIXEL,
            displayed=False,
            file_path=str(path),
            message="Sixel display requires Pillow: pip install Pillow",
        )

    try:
        img = Image.open(path)

        cell_w, cell_h = _get_cell_size()
        max_px_w = max_width * cell_w
        max_px_h = max_height * cell_h

        w, h = img.size
        if w > max_px_w or h > max_px_h:
            ratio = min(max_px_w / w, max_px_h / h)
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        img = img.convert("P", palette=Image.ADAPTIVE, colors=256)

        palette = img.getpalette()
        pixels = list(img.getdata())
        w, h = img.size

        sixel_data = _encode_sixel(pixels, palette, w, h)

        sys.stdout.write(f"\033P0;0;0q\"1;1;{w};{h}")
        sys.stdout.write(sixel_data)
        sys.stdout.write("\033\\")
        sys.stdout.write("\n")
        sys.stdout.flush()

        return DisplayResult(
            protocol=TerminalProtocol.SIXEL,
            displayed=True,
            file_path=str(path),
            message=f"Displayed via Sixel ({w}x{h})",
        )
    except Exception as e:
        return DisplayResult(
            protocol=TerminalProtocol.SIXEL,
            displayed=False,
            file_path=str(path),
            message=f"Sixel encoding failed: {e}",
        )


def _encode_sixel(pixels: list[int], palette: list[int] | None, width: int, height: int) -> str:
    logger.debug(f"Entered into _encode_sixel: {width}x{height}")
    lines: list[str] = []

    if palette:
        for i in range(0, min(len(palette), 768), 3):
            color_idx = i // 3
            r = int(palette[i] / 255 * 100)
            g = int(palette[i + 1] / 255 * 100)
            b = int(palette[i + 2] / 255 * 100)
            lines.append(f"#{color_idx};2;{r};{g};{b}")

    for band_y in range(0, height, 6):
        band_colors: dict[int, list[int]] = {}
        for dy in range(6):
            y = band_y + dy
            if y >= height:
                break
            for x in range(width):
                px = pixels[y * width + x]
                if px not in band_colors:
                    band_colors[px] = [0] * width
                band_colors[px][x] |= 1 << dy

        for color_idx, scanline in sorted(band_colors.items()):
            lines.append(f"#{color_idx}")
            for val in scanline:
                lines.append(chr(63 + val))
            lines.append("$")

        lines.append("-")

    return "".join(lines)


def _check_sixel_support() -> bool:
    logger.debug("Entered into _check_sixel_support")
    term = os.environ.get("TERM", "")
    if "sixel" in term.lower():
        return True
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    if term_program in ("mlterm", "contour", "foot", "wezterm"):
        return True
    return False


def _get_cell_size() -> tuple[int, int]:
    logger.debug("Entered into _get_cell_size")
    try:
        import fcntl
        import termios
        result = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
        rows, cols, xpixel, ypixel = struct.unpack("HHHH", result)
        if xpixel > 0 and ypixel > 0 and cols > 0 and rows > 0:
            return xpixel // cols, ypixel // rows
    except Exception:
        pass
    return 8, 16
