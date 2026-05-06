"""Voice generator: uses edge-tts to synthesise Vietnamese narration per scene."""
from __future__ import annotations

import asyncio
from pathlib import Path

import edge_tts
from tqdm import tqdm

from pipeline.scene_mapper import Scene

try:
    from moviepy.editor import AudioFileClip
    _MOVIEPY_AVAILABLE = True
except ImportError:
    _MOVIEPY_AVAILABLE = False


class VoiceGenerator:
    def __init__(self, config: dict, temp_dir: str = "temp") -> None:
        voice_cfg = config.get("voice", {})
        self._voice = voice_cfg.get("voice_id", "vi-VN-NamMinhNeural")
        self._rate = voice_cfg.get("rate", "+0%")
        self._pitch = voice_cfg.get("pitch", "+0Hz")
        self._audio_dir = Path(temp_dir) / "audio"
        self._audio_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, scenes: list[Scene]) -> list[Scene]:
        """Generate TTS for every scene in-place (sets scene.audio_path and
        scene.actual_duration). Returns the same list."""
        asyncio.run(self._generate_all_async(scenes))
        return scenes

    # ── async internals ───────────────────────────────────────────────────────

    async def _generate_all_async(self, scenes: list[Scene]) -> None:
        tasks = [self._generate_scene(scene) for scene in scenes]
        for coro in tqdm(
            asyncio.as_completed(tasks),
            total=len(tasks),
            desc="TTS",
            unit="scene",
        ):
            await coro

    async def _generate_scene(self, scene: Scene) -> None:
        out_path = self._audio_dir / f"scene_{scene.scene_index:04d}.mp3"

        if not out_path.exists():
            communicate = edge_tts.Communicate(
                scene.text,
                voice=self._voice,
                rate=self._rate,
                pitch=self._pitch,
            )
            await communicate.save(str(out_path))

        scene.audio_path = str(out_path)
        scene.actual_duration = self._get_audio_duration(out_path)

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_audio_duration(path: Path) -> float:
        if _MOVIEPY_AVAILABLE:
            try:
                clip = AudioFileClip(str(path))
                duration = clip.duration
                clip.close()
                return duration
            except Exception:
                pass
        # Fallback: use mutagen
        try:
            from mutagen.mp3 import MP3
            audio = MP3(str(path))
            return audio.info.length
        except Exception:
            pass
        # Last resort: estimate from word count (already in estimated_duration)
        return 0.0
