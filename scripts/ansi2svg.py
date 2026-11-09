#!/usr/bin/env python3
"""Render a `tmux capture-pane -e` dump (ANSI SGR colors) as an SVG terminal image.

    CAPTURE_ANSI=1 scripts/demo.sh --capture | scripts/ansi2svg.py > docs/screenshot.svg
"""

import html
import re
import sys

BG = "#1e1e2e"
FG = "#cdd6f4"
BASIC = ["#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de"]
BRIGHT = ["#7f849c", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8"]
CHAR_W, LINE_H, PAD = 8.4, 18, 16


def xterm256(n):
    if n < 8:
        return BASIC[n]
    if n < 16:
        return BRIGHT[n - 8]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        r, g, b = steps[n // 36], steps[n // 6 % 6], steps[n % 6]
        return f"#{r:02x}{g:02x}{b:02x}"
    v = 8 + (n - 232) * 10
    return f"#{v:02x}{v:02x}{v:02x}"


def parse(line):
    """Yield (text, fg, bg, bold, italic) runs."""
    fg, bg, bold, italic = None, None, False, False
    for part in re.split(r"(\x1b\[[0-9;]*m)", line):
        if not part.startswith("\x1b["):
            if part:
                yield part, fg, bg, bold, italic
            continue
        codes = [int(c) if c else 0 for c in part[2:-1].split(";")]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                fg, bg, bold, italic = None, None, False, False
            elif c == 1:
                bold = True
            elif c == 22:
                bold = False
            elif c == 3:
                italic = True
            elif c == 23:
                italic = False
            elif 30 <= c <= 37:
                fg = BASIC[c - 30]
            elif 90 <= c <= 97:
                fg = BRIGHT[c - 90]
            elif c == 39:
                fg = None
            elif 40 <= c <= 47:
                bg = BASIC[c - 40]
            elif c == 49:
                bg = None
            elif c in (38, 48) and i + 2 < len(codes) and codes[i + 1] == 5:
                if c == 38:
                    fg = xterm256(codes[i + 2])
                else:
                    bg = xterm256(codes[i + 2])
                i += 2
            i += 1


def main():
    lines = sys.stdin.read().rstrip("\n").split("\n")
    cols = max(len(re.sub(r"\x1b\[[0-9;]*m", "", l)) for l in lines)
    width = cols * CHAR_W + 2 * PAD
    height = len(lines) * LINE_H + 2 * PAD
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">',
        f'<rect width="{width:.0f}" height="{height:.0f}" rx="8" fill="{BG}"/>',
        '<g font-family="JetBrains Mono, Menlo, Consolas, monospace" font-size="14">',
    ]
    for row, line in enumerate(lines):
        y = PAD + row * LINE_H
        col = 0
        for text, fg, bg, bold, italic in parse(line):
            x = PAD + col * CHAR_W
            if bg:
                out.append(f'<rect x="{x:.1f}" y="{y}" width="{len(text) * CHAR_W:.1f}" height="{LINE_H}" fill="{bg}"/>')
            # One <text> per word, placed on its terminal cell. Avoids textLength (browsers
            # stretch runs with trailing spaces) and per-glyph x lists (Qt/Gwenview ignores them).
            weight = (' font-weight="bold"' if bold else "") + (' font-style="italic"' if italic else "")
            for m in re.finditer(r"\S+", text):
                out.append(
                    f'<text x="{x + m.start() * CHAR_W:.1f}" y="{y + 13}" fill="{fg or FG}"{weight}>'
                    f"{html.escape(m.group())}</text>"
                )
            col += len(text)
    out += ["</g>", "</svg>"]
    print("\n".join(out))


if __name__ == "__main__":
    main()
