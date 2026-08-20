#!/usr/bin/env python3
"""Generate a theme-aware neofetch-style SVG from profile.yaml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import yaml


CHAR_WIDTH = 9.7
FONT_SIZE = 16
LINE_HEIGHT = 22
PADDING_X = 26
PADDING_Y = 22
COLUMN_GAP = 34
LABEL_GAP = 2
MIN_INFO_COLUMNS = 38


class ConfigError(ValueError):
    """Raised when the profile configuration cannot be rendered."""


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value


def require_color(theme: dict[str, Any], mode: str, key: str) -> str:
    palette = require_mapping(theme.get(mode), f"theme.{mode}")
    value = palette.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"theme.{mode}.{key} must be a non-empty string")
    return value


def load_config(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    return require_mapping(data, "configuration")


def load_ascii(config_path: Path, profile: dict[str, Any]) -> list[str]:
    ascii_name = profile.get("ascii_file")
    if not isinstance(ascii_name, str) or not ascii_name:
        raise ConfigError("profile.ascii_file must be a non-empty path")

    ascii_path = (config_path.parent / ascii_name).resolve()
    try:
        return ascii_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ConfigError(f"ASCII art file not found: {ascii_path}") from exc


def validate_rows(rows: Any, max_rows: int) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ConfigError("rows must be a list")
    if len(rows) > max_rows:
        raise ConfigError(f"rows contains {len(rows)} entries; maximum is {max_rows}")

    validated: list[dict[str, Any]] = []
    for number, raw_row in enumerate(rows, start=1):
        row = require_mapping(raw_row, f"rows[{number}]")
        kinds = sum(
            (
                "text" in row,
                row.get("rule") is True,
                row.get("spacer") is True,
                "label" in row and "value" in row,
            )
        )
        if kinds != 1:
            raise ConfigError(
                f"rows[{number}] must define exactly one of text, rule, spacer, "
                "or label/value"
            )
        validated.append(row)
    return validated


def css(theme: dict[str, Any]) -> str:
    light = {key: require_color(theme, "light", key) for key in COLORS}
    dark = {key: require_color(theme, "dark", key) for key in COLORS}

    def variables(palette: dict[str, str]) -> str:
        return " ".join(f"--{key}: {value};" for key, value in palette.items())

    return f"""
    :root {{ color-scheme: light dark; {variables(light)} }}
    @media (prefers-color-scheme: dark) {{
      :root {{ {variables(dark)} }}
    }}
    .background {{ fill: var(--background); stroke: var(--muted); }}
    .ascii {{ fill: var(--accent); }}
    .header {{ fill: var(--accent); font-weight: 700; }}
    .label {{ fill: var(--label); }}
    .value {{ fill: var(--foreground); }}
    .rule {{ stroke: var(--muted); }}
    text {{
      font-family: "JetBrains Mono", "Cascadia Code", "Fira Code", monospace;
      font-size: {FONT_SIZE}px;
      font-variant-ligatures: none;
    }}
    """.strip()


COLORS = ("background", "foreground", "label", "accent", "muted")


def text_element(x: float, y: float, class_name: str, content: Any) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" class="{class_name}" '
        f'xml:space="preserve">{escape(str(content))}</text>'
    )


def render(config: dict[str, Any], config_path: Path) -> str:
    profile = require_mapping(config.get("profile"), "profile")
    theme = require_mapping(config.get("theme"), "theme")

    max_rows = profile.get("max_rows", 16)
    if not isinstance(max_rows, int) or max_rows < 1:
        raise ConfigError("profile.max_rows must be a positive integer")

    rows = validate_rows(config.get("rows"), max_rows)
    ascii_lines = load_ascii(config_path, profile)
    if len(ascii_lines) > max_rows:
        raise ConfigError(
            f"ASCII art contains {len(ascii_lines)} lines; maximum is {max_rows}"
        )

    ascii_columns = max((len(line) for line in ascii_lines), default=0)
    labels = [str(row["label"]) for row in rows if "label" in row]
    label_columns = max((len(label) for label in labels), default=0)

    def row_columns(row: dict[str, Any]) -> int:
        if "text" in row:
            return len(str(row["text"]))
        if "label" in row:
            return label_columns + LABEL_GAP + len(str(row["value"]))
        return 0

    info_columns = max(MIN_INFO_COLUMNS, *(row_columns(row) for row in rows))
    ascii_width = ascii_columns * CHAR_WIDTH
    info_width = info_columns * CHAR_WIDTH
    content_lines = max(len(ascii_lines), len(rows), 1)
    width = round(PADDING_X * 2 + ascii_width + COLUMN_GAP + info_width)
    height = round(PADDING_Y * 2 + content_lines * LINE_HEIGHT)
    info_x = PADDING_X + ascii_width + COLUMN_GAP
    value_x = info_x + (label_columns + LABEL_GAP) * CHAR_WIDTH
    first_baseline = PADDING_Y + FONT_SIZE

    elements: list[str] = []
    for index, line in enumerate(ascii_lines):
        y = first_baseline + index * LINE_HEIGHT
        elements.append(text_element(PADDING_X, y, "ascii", line))

    for index, row in enumerate(rows):
        y = first_baseline + index * LINE_HEIGHT
        if row.get("spacer") is True:
            continue
        if row.get("rule") is True:
            rule_y = y - FONT_SIZE / 3
            elements.append(
                f'<line x1="{info_x:.1f}" y1="{rule_y:.1f}" '
                f'x2="{info_x + info_width:.1f}" y2="{rule_y:.1f}" '
                'class="rule" stroke-width="1" />'
            )
            continue
        if "text" in row:
            style = str(row.get("style", "value"))
            if style not in {"header", "label", "value"}:
                raise ConfigError(
                    f"rows[{index + 1}].style must be header, label, or value"
                )
            elements.append(text_element(info_x, y, style, row["text"]))
            continue

        elements.append(text_element(info_x, y, "label", row["label"]))
        elements.append(text_element(value_x, y, "value", row["value"]))

    body = "\n    ".join(elements)
    title = escape(str(rows[0].get("text", "GitHub profile"))) if rows else "GitHub profile"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">{title}</title>
  <desc id="desc">Neofetch-style profile card with Linux ASCII art</desc>
  <style>{css(theme)}</style>
  <rect class="background" x="0.5" y="0.5" width="{width - 1}" height="{height - 1}"
        rx="8" stroke-width="1" />
  <g>
    {body}
  </g>
</svg>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("profile.yaml"))
    parser.add_argument("--output", type=Path, default=Path("profile.svg"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    try:
        svg = render(load_config(config_path), config_path)
        args.output.write_text(svg, encoding="utf-8")
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"generated {args.output} ({len(load_config(config_path)['rows'])} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
