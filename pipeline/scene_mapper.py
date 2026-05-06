"""Scene mapper: splits script sections into Scene objects with timing estimates."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pipeline.script_generator import ScriptSchema, SectionSchema


@dataclass
class Scene:
    # Identity
    section_id: str          # e.g. "section1"
    scene_index: int         # global scene index
    section_type: str        # hook | problem | content | solution | cta | pattern_interrupt | teaser

    # Content
    text: str                # narrator text for this scene chunk
    scene_prompt: str        # SD image prompt

    # Timing
    estimated_duration: float = 0.0   # seconds (from word count)
    actual_duration: float = 0.0      # filled in after TTS

    # Flags
    has_pattern_interrupt: bool = False
    pattern_interrupt_text: Optional[str] = None
    is_pattern_interrupt: bool = False   # True when this scene IS the PI flash
    is_teaser: bool = False

    # Audio / video paths (filled in later)
    audio_path: Optional[str] = None
    video_clip_path: Optional[str] = None


_WORDS_PER_SECOND = 130 / 60  # 130 wpm → ~2.17 words/sec


def _estimate_duration(text: str) -> float:
    word_count = len(text.split())
    return max(1.0, word_count / _WORDS_PER_SECOND)


class SceneMapper:
    """Maps a ScriptSchema into an ordered list of Scene objects.

    Strategy:
    - Each section becomes 1+ Scene (chunked at ~90s / ~195 words).
    - After each content-type scene, a pattern_interrupt Scene is inserted.
    - After each content-type scene, a teaser Scene is inserted.
    """

    MAX_WORDS_PER_CHUNK = 195   # ~90 seconds at 130 wpm

    def map(self, script: ScriptSchema) -> list[Scene]:
        scenes: list[Scene] = []
        index = 0

        for section in script.sections:
            chunks = self._chunk_narrator(section.narrator)

            for chunk_idx, chunk_text in enumerate(chunks):
                is_last_chunk = chunk_idx == len(chunks) - 1
                scene = Scene(
                    section_id=section.id,
                    scene_index=index,
                    section_type=section.type,
                    text=chunk_text,
                    scene_prompt=section.scene_prompt,
                    estimated_duration=_estimate_duration(chunk_text),
                    has_pattern_interrupt=(
                        is_last_chunk
                        and section.type == "content"
                        and section.pattern_interrupt is not None
                    ),
                    pattern_interrupt_text=(
                        section.pattern_interrupt
                        if is_last_chunk and section.type == "content"
                        else None
                    ),
                )
                scenes.append(scene)
                index += 1

                # Insert pattern interrupt flash scene after last content chunk
                if (
                    is_last_chunk
                    and section.type == "content"
                    and section.pattern_interrupt
                ):
                    pi_scene = Scene(
                        section_id=section.id,
                        scene_index=index,
                        section_type="pattern_interrupt",
                        text=section.pattern_interrupt,
                        scene_prompt=section.scene_prompt,  # reuse same visual
                        estimated_duration=_estimate_duration(section.pattern_interrupt),
                        is_pattern_interrupt=True,
                    )
                    scenes.append(pi_scene)
                    index += 1

                # Insert teaser scene after pattern interrupt / last content chunk
                if (
                    is_last_chunk
                    and section.type == "content"
                    and section.teaser
                ):
                    teaser_scene = Scene(
                        section_id=section.id,
                        scene_index=index,
                        section_type="teaser",
                        text=section.teaser,
                        scene_prompt=section.scene_prompt,
                        estimated_duration=_estimate_duration(section.teaser),
                        is_teaser=True,
                    )
                    scenes.append(teaser_scene)
                    index += 1

        return scenes

    # ── private ──────────────────────────────────────────────────────────────

    def _chunk_narrator(self, narrator: str) -> list[str]:
        """Split narrator text into chunks of at most MAX_WORDS_PER_CHUNK words,
        breaking at sentence boundaries where possible."""
        words = narrator.split()
        if len(words) <= self.MAX_WORDS_PER_CHUNK:
            return [narrator.strip()]

        # Split into sentences first
        import re
        sentences = re.split(r"(?<=[.!?…\n])\s+", narrator.strip())
        chunks: list[str] = []
        current_words: list[str] = []

        for sentence in sentences:
            s_words = sentence.split()
            if (
                current_words
                and len(current_words) + len(s_words) > self.MAX_WORDS_PER_CHUNK
            ):
                chunks.append(" ".join(current_words))
                current_words = s_words
            else:
                current_words.extend(s_words)

        if current_words:
            chunks.append(" ".join(current_words))

        return chunks if chunks else [narrator.strip()]
