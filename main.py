"""main.py — CLI entry point for the YouTube Anime Video pipeline.

Usage:
    python main.py "tại sao bạn luôn trì hoãn"
    python main.py "tại sao bạn luôn trì hoãn" --skip-visual
    python main.py "tại sao bạn luôn trì hoãn" --model qwen2.5:14b
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import typer
import yaml
from tqdm import tqdm

app = typer.Typer(add_completion=False, pretty_exceptions_short=True)

CONFIG_PATH = "config.yaml"


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _slug(text: str) -> str:
    """Convert a Vietnamese string to a safe filename slug."""
    # Basic ASCII-ification: keep lowercase letters, digits, underscores
    s = text.lower().strip()
    s = re.sub(r"[àáâãèéêìíòóôõùúăđĩũơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", "_", s)
    s = re.sub(r"[^a-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:60] if s else "video"


@app.command()
def run(
    topic: str = typer.Argument(..., help="Video topic in Vietnamese"),
    skip_visual: bool = typer.Option(False, "--skip-visual", help="Skip ComfyUI, use colour placeholders"),
    model: str = typer.Option("", "--model", help="Override Ollama model (e.g. qwen2.5:14b)"),
    output_dir: str = typer.Option("", "--output-dir", help="Override output directory"),
) -> None:
    """Generate a full 8–12 min YouTube anime video from a single topic string."""
    # ── Load config ───────────────────────────────────────────────────────────
    config = _load_config()
    if skip_visual:
        config["skip_visual"] = True
    if model:
        config["ollama"]["model"] = model
    if output_dir:
        config["output"]["output_dir"] = output_dir

    temp_dir: str = config["output"].get("temp_dir", "temp")
    out_dir: str = config["output"].get("output_dir", "output")
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    slug = _slug(topic)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_output = str(Path(out_dir) / f"{slug}_{timestamp}.mp4")

    typer.echo(f"\n🎬 Topic   : {topic}")
    typer.echo(f"   Model   : {config['ollama']['model']}")
    typer.echo(f"   Visual  : {'SKIP (placeholders)' if config.get('skip_visual') else 'ComfyUI AnimateDiff'}")
    typer.echo(f"   Output  : {final_output}\n")

    # ── Step 1: Script generation ─────────────────────────────────────────────
    typer.echo("▶ [1/6] Generating script via Ollama…")
    from pipeline.script_generator import ScriptGenerator
    generator = ScriptGenerator(config)
    script = generator.generate(topic)

    script_dump = Path(temp_dir) / "script.json"
    script_dump.write_text(
        json.dumps(script.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    typer.echo(f"   ✓ Script saved → {script_dump}")
    typer.echo(f"   Title  : {script.title}")
    typer.echo(f"   Sections: {len(script.sections)}")

    # ── Step 2: Scene mapping ─────────────────────────────────────────────────
    typer.echo("\n▶ [2/6] Mapping scenes…")
    from pipeline.scene_mapper import SceneMapper
    mapper = SceneMapper()
    scenes = mapper.map(script)
    typer.echo(f"   ✓ {len(scenes)} scenes created")

    # ── Step 3: Voice generation ──────────────────────────────────────────────
    typer.echo("\n▶ [3/6] Generating voice (edge-tts)…")
    from pipeline.voice_generator import VoiceGenerator
    voice_gen = VoiceGenerator(config, temp_dir=temp_dir)
    scenes = voice_gen.generate_all(scenes)
    total_dur = sum(s.actual_duration for s in scenes)
    typer.echo(f"   ✓ Voice done — total estimated duration: {total_dur:.0f}s ({total_dur/60:.1f} min)")

    # ── Step 4: Visual generation ─────────────────────────────────────────────
    typer.echo("\n▶ [4/6] Generating visuals (ComfyUI)…")
    from pipeline.visual_engine import VisualEngine
    visual_engine = VisualEngine(config, temp_dir=temp_dir)
    scenes = visual_engine.generate_all(scenes)
    typer.echo("   ✓ Visuals done")

    # ── Step 5: Subtitles ─────────────────────────────────────────────────────
    typer.echo("\n▶ [5/6] Generating subtitles (.ass)…")
    from pipeline.subtitle_engine import SubtitleEngine
    sub_engine = SubtitleEngine(config)
    subtitle_path = sub_engine.generate(scenes, temp_dir=temp_dir)
    typer.echo(f"   ✓ Subtitles saved → {subtitle_path}")

    # ── Step 6: Render ────────────────────────────────────────────────────────
    typer.echo("\n▶ [6/6] Rendering final video (FFmpeg)…")
    from pipeline.renderer import Renderer
    renderer = Renderer(config, temp_dir=temp_dir)
    renderer.render(scenes, str(subtitle_path), final_output)

    typer.echo(f"\n✅ Done! Video saved to: {final_output}")
    typer.echo(f"   Duration: ~{total_dur/60:.1f} min | Scenes: {len(scenes)}")


if __name__ == "__main__":
    app()
