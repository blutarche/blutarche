#!/usr/bin/env python3
"""Restyle github-profile-3d-contrib SVGs with the studio palette and fonts.

Usage: restyle.py FILE.svg [FILE.svg ...]

The generator paints each language with its GitHub linguist color. Slices and
legend rects are emitted in the same order (biggest first, optional trailing
"other"), so colors are assigned by position, never by the color they hold.
Theme is picked from the basename: dark.svg uses the dark ramp, anything else
the light one.

The generator's font rule is replaced by base64 @font-face data URIs (GitHub
serves the SVGs through camo <img>, which cannot fetch external fonts) read
from the fonts/ directory next to this script. A file that already carries the
injected fonts keeps them, so reruns are byte-identical.

Calendar bars (days at contribution level 1-4) get a one-shot CSS entrance whose
delay follows the day's screen x, so the year sweeps in left to right. Reduced
motion swaps the rise for an opacity-only fade. Level 0 floor tiles stay static.

Exits non-zero, writing nothing, if any file's markup is not what the
generator is known to emit.
"""

import base64
import os
import re
import sys
from pathlib import Path

RAMPS = {
    "light": ["#7C70F5", "#F0A8D0", "#5A4FC0", "#9088F8", "#34343F"],
    "dark": ["#7C70F5", "#F0A8D0", "#5A4FC0", "#9088F8", "#F2EFEA"],
}
OTHER_COLOR = "#8C8C94"
OTHER_LABEL = "other"

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_FACES = [
    ("Inter", 400, "inter-regular.woff2"),
    ("JetBrains Mono", 500, "jetbrains-mono-medium.woff2"),
    ("Space Grotesk", 700, "space-grotesk-bold.woff2"),
]
GENERATOR_FONT_RULE = '* { font-family: "Ubuntu", "Helvetica", "Arial", sans-serif; }'
INJECTED_FONT_MARKER = '@font-face { font-family: "Inter";'

SWEEP_MS = 1000
BAR_CSS = """@keyframes bar-rise { from { opacity: 0; translate: 0 16px; } }
@keyframes bar-fade { from { opacity: 0; } }
.bar { animation: bar-rise 500ms cubic-bezier(0.23, 1, 0.32, 1) var(--d) both; }
@media (prefers-reduced-motion: reduce) { .bar { animation: bar-fade 200ms cubic-bezier(0.23, 1, 0.32, 1) both; } }"""

SLICE_RE = re.compile(r'(style="fill: )#[0-9A-Fa-f]{6}(;" class="stroke-bg")')
LEGEND_RECT_RE = re.compile(r'(fill=")#[0-9A-Fa-f]{6}(" class="stroke-bg")')
TEXT_RE = re.compile(r"<text[^>]*>([^<]*)</text>")
DAY_GROUP_RE = re.compile(
    r'<g transform="translate\((-?\d+(?:\.\d+)?) -?\d+(?:\.\d+)?\)">'
    r'(?=<rect stroke="none"[^>]*class="cont-top-([0-4])")'
)


class RestyleError(Exception):
    pass


def legend_labels(svg: str) -> list[str]:
    """Labels of the legend group, which holds only the rects followed by the texts."""
    rects = list(LEGEND_RECT_RE.finditer(svg))
    start = svg.rfind("<g", 0, rects[0].start())
    end = svg.find("</g>", rects[-1].end())
    if start == -1 or end == -1:
        raise RestyleError("could not locate the legend group")
    return TEXT_RE.findall(svg[start:end])


def color_for(index: int, label: str, ramp: list[str]) -> str:
    if label.strip() == OTHER_LABEL:
        return OTHER_COLOR
    if index >= len(ramp):
        raise RestyleError(f"legend entry {index} ({label!r}) has no ramp color; ramp has {len(ramp)}")
    return ramp[index]


def recolor(svg: str, theme: str) -> str:
    ramp = RAMPS[theme]
    slices = SLICE_RE.findall(svg)
    rects = LEGEND_RECT_RE.findall(svg)
    if not slices:
        raise RestyleError("no pie slices found (generator markup changed?)")
    labels = legend_labels(svg) if rects else []
    if not (len(slices) == len(rects) == len(labels)):
        raise RestyleError(
            f"pie slices ({len(slices)}), legend rects ({len(rects)}) and legend labels ({len(labels)}) differ"
        )
    colors = [color_for(i, label, ramp) for i, label in enumerate(labels)]

    def painter(pattern: re.Pattern[str], text: str) -> str:
        counter = iter(colors)
        return pattern.sub(lambda m: f"{m.group(1)}{next(counter)}{m.group(2)}", text)

    return painter(LEGEND_RECT_RE, painter(SLICE_RE, svg))


def font_css() -> str:
    faces = []
    for family, weight, filename in FONT_FACES:
        data = base64.b64encode((FONT_DIR / filename).read_bytes()).decode("ascii")
        faces.append(
            f'@font-face {{ font-family: "{family}"; font-weight: {weight}; '
            f'src:url(data:font/woff2;base64,{data}) format("woff2"); }}'
        )
    rules = [
        '* { font-family: "Inter", sans-serif; }',
        '.fill-weak { font-family: "JetBrains Mono", monospace; }',
        'text[style*="bold"] { font-family: "Space Grotesk", sans-serif; }',
    ]
    return "\n".join(faces + rules)


def restyle_fonts(svg: str, css: str) -> str:
    if GENERATOR_FONT_RULE in svg:
        if svg.count(GENERATOR_FONT_RULE) != 1:
            raise RestyleError("generator font rule appears more than once")
        return svg.replace(GENERATOR_FONT_RULE, css)
    if INJECTED_FONT_MARKER in svg:
        return svg
    raise RestyleError("neither the generator font rule nor the injected fonts found (generator markup changed?)")


def animate_bars(svg: str) -> str:
    if BAR_CSS in svg:
        return svg
    days = list(DAY_GROUP_RE.finditer(svg))
    if not days:
        raise RestyleError("no calendar day groups found (generator markup changed?)")
    if svg.count("</style>") != 1:
        raise RestyleError("expected exactly one </style>")

    xs = [float(m.group(1)) for m in days]
    low = min(xs)
    span = (max(xs) - low) or 1

    def tag(m: re.Match[str]) -> str:
        if m.group(2) == "0":
            return m.group(0)
        delay = round((float(m.group(1)) - low) / span * SWEEP_MS)
        return f'{m.group(0)[:-1]} class="bar" style="--d:{delay}ms">'

    return DAY_GROUP_RE.sub(tag, svg).replace("</style>", f"\n{BAR_CSS}</style>")


def main(paths: list[str]) -> int:
    if not paths:
        print("usage: restyle.py FILE.svg [FILE.svg ...]", file=sys.stderr)
        return 2

    try:
        css = font_css()
    except OSError as err:
        print(f"restyle: fonts: {err}", file=sys.stderr)
        return 1

    results: dict[str, str] = {}
    failed = False
    for path in paths:
        theme = "dark" if os.path.basename(path) == "dark.svg" else "light"
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                original = fh.read()
            results[path] = animate_bars(restyle_fonts(recolor(original, theme), css))
        except (OSError, RestyleError) as err:
            print(f"restyle: {path}: {err}", file=sys.stderr)
            failed = True
    if failed:
        return 1

    for path, svg in results.items():
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(svg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
