"""
visual_cards.py — branded quote-card generator (Pillow).
Renders tweet text onto a 1600x900 branded PNG for higher engagement.
Used by social_core.autopost(..., with_visual=True).
"""

import os
import textwrap
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

CARD_W, CARD_H = 1600, 900
MARGIN = 110

FONT_BOLD    = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"

OUT_DIR = Path(__file__).resolve().parent / "social_data" / "cards"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))


def _strip_url(text: str, url: str) -> str:
    """Remove the trailing URL from the card body (footer shows it instead)."""
    t = text.replace(url, "").strip()
    # tidy trailing punctuation/space left behind
    return t.strip(" .,\n")


def _fit_text(draw, text, font_path, max_w, max_h, start_size=72, min_size=34):
    """Find the largest font size + wrapping that fits the text box."""
    size = start_size
    while size >= min_size:
        font = _font(font_path, size)
        # estimate chars per line from average glyph width
        avg = draw.textlength("x" * 20, font=font) / 20
        chars_per_line = max(10, int(max_w / max(avg, 1)))
        wrapped = textwrap.fill(text, width=chars_per_line)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=14)
        h = bbox[3] - bbox[1]
        w = bbox[2] - bbox[0]
        if h <= max_h and w <= max_w:
            return font, wrapped
        size -= 4
    font = _font(font_path, min_size)
    avg = draw.textlength("x" * 20, font=font) / 20
    chars_per_line = max(10, int(max_w / max(avg, 1)))
    return font, textwrap.fill(text, width=chars_per_line)


def make_card(brand, tweet_text: str) -> str:
    """Render a branded quote card for `tweet_text`. Returns the PNG path."""
    theme = brand.theme or {}
    bg     = _hex(theme.get("bg", "#0A0E14"))
    fg     = _hex(theme.get("fg", "#FFFFFF"))
    accent = _hex(theme.get("accent", "#F5C518"))
    label  = theme.get("label", brand.name.upper())
    footer = theme.get("footer", brand.url)

    img = Image.new("RGB", (CARD_W, CARD_H), bg)
    draw = ImageDraw.Draw(img)

    # Accent bar top-left
    draw.rectangle([MARGIN, MARGIN, MARGIN + 90, MARGIN + 12], fill=accent)

    # Brand label
    label_font = _font(FONT_BOLD, 34)
    draw.text((MARGIN, MARGIN + 30), label, font=label_font, fill=accent)

    # Body text — fit into the central area
    body = _strip_url(tweet_text, brand.url)
    box_top = MARGIN + 110
    box_h = CARD_H - box_top - 150
    body_font, wrapped = _fit_text(
        draw, body, FONT_BOLD, CARD_W - 2 * MARGIN, box_h
    )
    draw.multiline_text((MARGIN, box_top), wrapped, font=body_font,
                        fill=fg, spacing=16)

    # Footer: URL + handle
    foot_font = _font(FONT_REGULAR, 32)
    draw.line([MARGIN, CARD_H - 110, CARD_W - MARGIN, CARD_H - 110],
              fill=accent, width=2)
    draw.text((MARGIN, CARD_H - 85), footer, font=_font(FONT_BOLD, 36), fill=fg)
    handle = f"@{brand.handle}"
    hw = draw.textlength(handle, font=foot_font)
    draw.text((CARD_W - MARGIN - hw, CARD_H - 83), handle, font=foot_font, fill=accent)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"{brand.key}_{ts}.png"
    img.save(out, "PNG")
    return str(out)


if __name__ == "__main__":
    # Quick visual test for both brands
    import sys
    from brands import BRANDS
    key = sys.argv[1] if len(sys.argv) > 1 else "apex"
    sample = sys.argv[2] if len(sys.argv) > 2 else (
        "Stop paying for human availability when you can own an agent that "
        "never clocks out. Swap your VA and tool stack for one platform. swarmsfall.com"
    )
    path = make_card(BRANDS[key], sample)
    print(f"Card written: {path}")
