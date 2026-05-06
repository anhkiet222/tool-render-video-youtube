"""Subtitle engine: generates an ASS subtitle file with keyword highlights."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from pipeline.scene_mapper import Scene

# ── ASS header template ───────────────────────────────────────────────────────

_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fontsize},{primary},{secondary},{outline},{shadow},{bold},0,0,0,100,100,0,0,1,{outline_px},{shadow_px},2,10,10,{margin_v},1
Style: Interrupt,{font},{interrupt_size},{interrupt_color},{secondary},{outline},{shadow},1,0,0,0,100,100,0,0,1,{outline_px},{shadow_px},2,10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(seconds: float) -> str:
    """Convert float seconds to ASS timestamp H:MM:SS.cc"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


# Words that should be highlighted (all-caps in script or surrounded by quotes)
_KEYWORD_RE = re.compile(r"\b([A-ZÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚĂĐĨŨƠƯẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ]{3,})\b|"
                         r"['""]([^'""\n]+)['""]")

_KEYWORD_COLOR = "{\\c&H00FFFF&\\b1}"  # cyan bold
_RESET_COLOR   = "{\\c&HFFFFFF&\\b0}"  # back to white, unbold


def _apply_keyword_highlights(text: str, keyword_color: str) -> str:
    def replacer(m: re.Match) -> str:
        word = m.group(1) or m.group(2)
        return f"{keyword_color}{word}{_RESET_COLOR}"

    return _KEYWORD_RE.sub(replacer, text)


class SubtitleEngine:
    def __init__(self, config: dict) -> None:
        sub = config.get("subtitle", {})
        out_cfg = config.get("output", {})
        self._width = out_cfg.get("width", 1280)
        self._height = out_cfg.get("height", 720)
        self._font = sub.get("font_name", "Arial")
        self._fontsize = sub.get("font_size", 22)
        self._primary = sub.get("primary_color", "&H00FFFFFF")
        self._outline_color = sub.get("outline_color", "&H00000000")
        self._shadow_color = sub.get("shadow_color", "&H80000000")
        self._bold = sub.get("bold", 0)
        self._outline_px = sub.get("outline", 2)
        self._shadow_px = sub.get("shadow", 1)
        self._margin_v = sub.get("margin_v", 40)
        self._keyword_color_code = "{\\c" + sub.get("keyword_color", "&H0000FFFF&").replace("&H", "&H").replace("&", "&") + "\\b1}"
        self._interrupt_color = sub.get("interrupt_color", "&H000055FF")
        self._interrupt_size = sub.get("interrupt_font_size", 28)

    def generate(self, scenes: list[Scene], temp_dir: str = "temp") -> Path:
        """Build an ASS subtitle file from scene list. Returns the path."""
        out_path = Path(temp_dir) / "subtitles.ass"

        header = _ASS_HEADER.format(
            width=self._width,
            height=self._height,
            font=self._font,
            fontsize=self._fontsize,
            primary=self._primary,
            secondary="&H00000000",
            outline=self._outline_color,
            shadow=self._shadow_color,
            bold=self._bold,
            outline_px=self._outline_px,
            shadow_px=self._shadow_px,
            margin_v=self._margin_v,
            interrupt_size=self._interrupt_size,
            interrupt_color=self._interrupt_color,
        )

        lines: list[str] = []
        cursor = 0.0  # current timestamp in seconds

        for scene in scenes:
            duration = scene.actual_duration or scene.estimated_duration
            if duration <= 0:
                duration = 3.0

            style = "Interrupt" if scene.is_pattern_interrupt else "Default"
            sentences = self._split_sentences(scene.text)
            if not sentences:
                cursor += duration
                continue

            time_per_sentence = duration / len(sentences)
            for sent in sentences:
                start = cursor
                end = cursor + time_per_sentence
                # Apply keyword highlights for Default style
                display_text = sent
                if style == "Default":
                    display_text = self._highlight_keywords(sent)
                lines.append(
                    f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,{display_text}"
                )
                cursor = end

        out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
        return out_path

    # ── private ───────────────────────────────────────────────────────────────

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into short display chunks (1–2 lines per cue)."""
        # Split on sentence-ending punctuation
        parts = re.split(r"(?<=[.!?…\n])\s*", text.strip())
        result: list[str] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # Further split long sentences at natural break points
            words = part.split()
            if len(words) <= 12:
                result.append(part)
            else:
                # Split roughly in half at nearest punctuation or middle
                mid = len(words) // 2
                result.append(" ".join(words[:mid]))
                result.append(" ".join(words[mid:]))
        return result if result else [text.strip()]

    def _highlight_keywords(self, text: str) -> str:
        """Wrap keywords in ASS override tags for colour/bold."""
        highlighted = _apply_keyword_highlights(text, self._keyword_color_code)
        return highlighted
