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


def _hex(c, fallback=(0, 0, 0)):
    """Parse a #RRGGBB or #RGB hex color. Falls back gracefully on bad input."""
    try:
        c = str(c).strip().lstrip("#")
        if len(c) == 3:  # shorthand #RGB -> #RRGGBB
            c = "".join(ch * 2 for ch in c)
        if len(c) < 6:
            return fallback
        return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))
    except (ValueError, TypeError):
        return fallback


def _strip_url(text: str, url: str) -> str:
    """Remove the trailing URL from the card body (footer shows it instead)."""
    t = text.replace(url, "").strip()
    # tidy trailing punctuation/space left behind
    return t.strip(" .,\n")


def _wrap(draw, text, font, max_w):
    """Greedy word wrap measured in real pixels (the old char-count estimate
    used the narrow glyph 'x' and let wide text overflow the right edge).
    Over-long unbroken words are split by character."""
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            # split a single word that is wider than the box
            while draw.textlength(word, font=font) > max_w and len(word) > 1:
                cut = len(word) - 1
                while cut > 1 and draw.textlength(word[:cut], font=font) > max_w:
                    cut -= 1
                if cur:
                    lines.append(cur); cur = ""
                lines.append(word[:cut]); word = word[cut:]
            trial = f"{cur} {word}".strip()
            if cur and draw.textlength(trial, font=font) > max_w:
                lines.append(cur); cur = word
            else:
                cur = trial
        lines.append(cur)
    return "\n".join(lines)


def _truncate_to_height(draw, text, font, max_w, max_h, spacing):
    """Drop trailing lines (with an ellipsis) until the block fits max_h."""
    lines = text.split("\n")
    while lines:
        candidate = "\n".join(lines)
        bbox = draw.multiline_textbbox((0, 0), candidate, font=font, spacing=spacing)
        if (bbox[3] - bbox[1]) <= max_h:
            return candidate
        lines.pop()
        if lines:
            lines[-1] = lines[-1].rstrip() + "…"
    return ""


def _fit_text(draw, text, font_path, max_w, max_h, start_size=72, min_size=34):
    """Find the largest font size + wrapping that fits the text box.
    Guarantees the returned text never overflows max_h (truncates if needed)."""
    text = (text or "").strip()
    if not text:
        text = " "
    size = start_size
    while size >= min_size:
        font = _font(font_path, size)
        wrapped = _wrap(draw, text, font, max_w)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=14)
        h = bbox[3] - bbox[1]
        w = bbox[2] - bbox[0]
        if h <= max_h and w <= max_w:
            return font, wrapped
        size -= 4
    # Even at min size it doesn't fit — wrap at min size and hard-truncate height.
    font = _font(font_path, min_size)
    wrapped = _wrap(draw, text, font, max_w)
    wrapped = _truncate_to_height(draw, wrapped, font, max_w, max_h, spacing=14)
    return font, wrapped


def make_card(brand, tweet_text: str) -> str:
    """Render a branded quote card for `tweet_text`. Returns the PNG path."""
    theme = brand.theme or {}
    bg     = _hex(theme.get("bg", "#0A0E14"), fallback=(10, 14, 20))
    fg     = _hex(theme.get("fg", "#FFFFFF"), fallback=(255, 255, 255))
    accent = _hex(theme.get("accent", "#F5C518"), fallback=(245, 197, 24))
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
    body = _strip_url(tweet_text or "", brand.url)
    if not body:
        # Nothing left after stripping the URL — show the brand name rather than a blank card.
        body = brand.name
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
