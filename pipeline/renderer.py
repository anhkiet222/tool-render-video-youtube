"""Renderer: assembles scenes into the final 1280x720 MP4 using FFmpeg subprocess calls."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from pipeline.scene_mapper import Scene

# ── section type → BGM track key mapping ─────────────────────────────────────
_SECTION_BGM = {
    "hook": "dark",
    "problem": "dark",
    "content": "neutral",
    "pattern_interrupt": "neutral",
    "teaser": "neutral",
    "solution": "uplifting",
    "cta": "uplifting",
}


def _ffmpeg(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    return subprocess.run(cmd, check=check, capture_output=True)


def _probe_duration(path: str) -> float:
    """Return media duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", path,
        ],
        capture_output=True,
        check=True,
    )
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


class Renderer:
    def __init__(self, config: dict, temp_dir: str = "temp") -> None:
        out_cfg = config.get("output", {})
        bgm_cfg = config.get("bgm", {})
        trans_cfg = config.get("transitions", {})
        self._width = out_cfg.get("width", 1280)
        self._height = out_cfg.get("height", 720)
        self._fps = out_cfg.get("fps", 24)
        self._crf = out_cfg.get("crf", 23)
        self._vcodec = out_cfg.get("video_codec", "libx264")
        self._acodec = out_cfg.get("audio_codec", "aac")
        self._abitrate = out_cfg.get("audio_bitrate", "192k")
        self._bgm_volume = bgm_cfg.get("volume", 0.20)
        self._bgm_tracks: dict[str, str] = bgm_cfg.get("tracks", {})
        self._section_bgm_map: dict[str, str] = config.get("section_bgm_map", _SECTION_BGM)
        self._fade_dur = trans_cfg.get("fade_duration", 0.3)
        self._glitch_dur = trans_cfg.get("pattern_interrupt_flash_duration", 0.5)
        self._temp_dir = Path(temp_dir)
        self._render_dir = self._temp_dir / "render"
        self._render_dir.mkdir(parents=True, exist_ok=True)

    def render(
        self,
        scenes: list[Scene],
        subtitle_path: str,
        output_path: str,
    ) -> str:
        """Full render pipeline. Returns path to final MP4."""
        # Step 1: Build per-scene video segments (loop + voice)
        segment_paths = self._build_segments(scenes)

        # Step 2: Concatenate with transitions
        concat_video = self._concat_segments(segment_paths, scenes)

        # Step 3: Mix BGM
        mixed_audio = self._build_mixed_audio(scenes)

        # Step 4: Combine video + audio + subtitles
        self._combine(concat_video, mixed_audio, subtitle_path, output_path)

        return output_path

    # ── Step 1: per-scene segment ─────────────────────────────────────────────

    def _build_segments(self, scenes: list[Scene]) -> list[Path]:
        paths: list[Path] = []
        for scene in scenes:
            out = self._render_dir / f"seg_{scene.scene_index:04d}.mp4"
            if out.exists():
                paths.append(out)
                continue

            duration = scene.actual_duration or scene.estimated_duration or 3.0
            audio_path = scene.audio_path
            clip_path = scene.video_clip_path

            if not clip_path or not Path(clip_path).exists():
                # Generate a solid-color placeholder
                clip_path = self._make_color_clip(scene.section_type, duration, scene.scene_index)

            if not audio_path or not Path(audio_path).exists():
                # Silent audio placeholder
                audio_path = self._make_silent_audio(duration, scene.scene_index)

            # Loop the visual clip to match audio duration, scale to output size
            filter_v = (
                f"scale={self._width}:{self._height}:force_original_aspect_ratio=decrease,"
                f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1"
            )

            if scene.is_pattern_interrupt:
                filter_v += f",zoompan=z='min(zoom+0.002,1.3)':d={int(duration * self._fps)}:s={self._width}x{self._height}:fps={self._fps}"
                filter_v += ",curves=all='0/0 0.5/1 1/0'"  # flash white effect

            _ffmpeg(
                "-stream_loop", "-1",
                "-i", clip_path,
                "-i", audio_path,
                "-vf", filter_v,
                "-c:v", self._vcodec,
                "-c:a", self._acodec,
                "-b:a", self._abitrate,
                "-t", str(duration),
                "-shortest",
                "-r", str(self._fps),
                "-crf", str(self._crf),
                str(out),
            )
            paths.append(out)
        return paths

    # ── Step 2: concatenate segments ──────────────────────────────────────────

    def _concat_segments(self, segment_paths: list[Path], scenes: list[Scene]) -> Path:
        out = self._render_dir / "concat_raw.mp4"
        if out.exists():
            return out

        if len(segment_paths) == 1:
            shutil.copy(segment_paths[0], out)
            return out

        # Build xfade filter chain for crossfades at section boundaries
        # Find section boundary indices (where section_id changes)
        boundary_indices: set[int] = set()
        for i in range(1, len(scenes)):
            if scenes[i].section_id != scenes[i - 1].section_id:
                boundary_indices.add(i)

        # Build cumulative offsets for xfade
        durations = [_probe_duration(str(p)) for p in segment_paths]

        # Build complex filtergraph with xfade
        filter_parts: list[str] = []
        inputs_cmd: list[str] = []
        for p in segment_paths:
            inputs_cmd += ["-i", str(p)]

        n = len(segment_paths)
        if n <= 1:
            shutil.copy(segment_paths[0], out)
            return out

        # Build xfade chain
        filter_graph = ""
        offset = durations[0] - self._fade_dur
        prev_label = "[0:v]"
        for i in range(1, n):
            label_out = f"[v{i}]"
            use_fade = i in boundary_indices
            if use_fade:
                filter_graph += (
                    f"{prev_label}[{i}:v]xfade=transition=fade:duration={self._fade_dur}"
                    f":offset={offset:.3f}{label_out};"
                )
            else:
                filter_graph += (
                    f"{prev_label}[{i}:v]xfade=transition=fade:duration=0.05"
                    f":offset={offset:.3f}{label_out};"
                )
            prev_label = label_out
            if i < n - 1:
                offset += durations[i] - self._fade_dur

        # Audio concat (simple)
        audio_filter = "".join(f"[{i}:a]" for i in range(n))
        audio_filter += f"concat=n={n}:v=0:a=1[aout]"

        filter_complex = filter_graph + audio_filter

        _ffmpeg(
            *inputs_cmd,
            "-filter_complex", filter_complex,
            "-map", prev_label,
            "-map", "[aout]",
            "-c:v", self._vcodec,
            "-c:a", self._acodec,
            "-b:a", self._abitrate,
            "-crf", str(self._crf),
            "-r", str(self._fps),
            str(out),
        )
        return out

    # ── Step 3: build mixed BGM audio ─────────────────────────────────────────

    def _build_mixed_audio(self, scenes: list[Scene]) -> Path:
        """Concatenate per-section voice audio with BGM mixed at low volume."""
        out = self._render_dir / "mixed_audio.aac"
        if out.exists():
            return out

        # Voice track already embedded per-segment; here we build a full BGM-only
        # track then merge it with the final video in Step 4.
        # Compute total duration
        total_dur = sum(
            (s.actual_duration or s.estimated_duration or 3.0) for s in scenes
        )

        # Check if any BGM file exists
        bgm_path = self._pick_bgm_path("neutral")
        if bgm_path and Path(bgm_path).exists():
            # Loop BGM to match total duration, apply volume
            _ffmpeg(
                "-stream_loop", "-1",
                "-i", bgm_path,
                "-af", f"volume={self._bgm_volume}",
                "-t", str(total_dur),
                "-c:a", self._acodec,
                "-b:a", self._abitrate,
                str(out),
            )
        else:
            # Silent BGM placeholder
            _ffmpeg(
                "-f", "lavfi",
                "-i", f"aevalsrc=0:c=stereo:s=44100:d={total_dur}",
                "-c:a", self._acodec,
                "-b:a", self._abitrate,
                str(out),
            )
        return out

    # ── Step 4: final combine ─────────────────────────────────────────────────

    def _combine(
        self,
        video_path: Path,
        bgm_path: Path,
        subtitle_path: str,
        output_path: str,
    ) -> None:
        """Merge video (with embedded voice), BGM layer, and subtitles."""
        # Escape subtitle path for FFmpeg filter (forward slashes, escape colons on Windows)
        sub_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:")

        _ffmpeg(
            "-i", str(video_path),
            "-i", str(bgm_path),
            # Mix: stream 0 audio (voice) + stream 1 audio (BGM)
            "-filter_complex",
            f"[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=3[amixed];"
            f"[0:v]ass={sub_escaped}[vout]",
            "-map", "[vout]",
            "-map", "[amixed]",
            "-c:v", self._vcodec,
            "-c:a", self._acodec,
            "-b:a", self._abitrate,
            "-crf", str(self._crf),
            "-r", str(self._fps),
            output_path,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _pick_bgm_path(self, track_key: str) -> str | None:
        return self._bgm_tracks.get(track_key)

    def _make_color_clip(self, section_type: str, duration: float, idx: int) -> str:
        """Generate a solid-color video as visual placeholder via FFmpeg."""
        color_map = {
            "hook": "0x0a0a1a",
            "problem": "0x0a0a1a",
            "content": "0x0d0d1f",
            "pattern_interrupt": "0x1a0000",
            "solution": "0x001a0a",
            "cta": "0x001520",
            "teaser": "0x0d0d1f",
        }
        color = color_map.get(section_type, "0x0a0a1a")
        out = self._render_dir / f"placeholder_{idx:04d}.mp4"
        _ffmpeg(
            "-f", "lavfi",
            "-i", f"color=c={color}:size={self._width}x{self._height}:r={self._fps}:d={duration}",
            "-c:v", self._vcodec,
            "-crf", str(self._crf),
            str(out),
        )
        return str(out)

    def _make_silent_audio(self, duration: float, idx: int) -> str:
        out = self._render_dir / f"silent_{idx:04d}.aac"
        _ffmpeg(
            "-f", "lavfi",
            "-i", f"aevalsrc=0:c=stereo:s=44100:d={duration}",
            "-c:a", self._acodec,
            "-b:a", self._abitrate,
            str(out),
        )
        return str(out)
