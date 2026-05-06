"""Visual engine: generates anime images via ComfyUI SD, then animates with AnimateDiff."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from tqdm import tqdm

from pipeline.scene_mapper import Scene


class ComfyUIClient:
    """Thin wrapper around the ComfyUI REST API. Supports local and cloud (RunPod/Vast.ai)."""

    def __init__(self, base_url: str, timeout: int = 600, api_key: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client_id = str(uuid.uuid4())
        # RunPod and Vast.ai require Authorization header for public proxy URLs
        self._headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def queue_prompt(self, workflow: dict) -> str:
        """Submit a workflow and return the prompt_id."""
        payload = {"prompt": workflow, "client_id": self._client_id}
        resp = httpx.post(
            f"{self._base}/prompt",
            json=payload,
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["prompt_id"]

    def wait_for_completion(self, prompt_id: str, poll_sec: float = 2.0) -> dict:
        """Poll /history until the prompt finishes. Returns the output dict."""
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            resp = httpx.get(
                f"{self._base}/history/{prompt_id}",
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            history = resp.json()
            if prompt_id in history:
                return history[prompt_id]["outputs"]
            time.sleep(poll_sec)
        raise TimeoutError(f"ComfyUI prompt {prompt_id} timed out after {self._timeout}s")

    def get_image_bytes(self, filename: str, subfolder: str = "", image_type: str = "output") -> bytes:
        resp = httpx.get(
            f"{self._base}/view",
            params={"filename": filename, "subfolder": subfolder, "type": image_type},
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content

    def get_video_bytes(self, filename: str, subfolder: str = "", image_type: str = "output") -> bytes:
        return self.get_image_bytes(filename, subfolder, image_type)


class VisualEngine:
    """Generates a per-scene animated video clip via ComfyUI."""

    def __init__(self, config: dict, temp_dir: str = "temp") -> None:
        cu_cfg = config["comfyui"]
        ad_cfg = config["animatediff"]
        self._client = ComfyUIClient(
            base_url=cu_cfg["base_url"],
            timeout=cu_cfg.get("timeout_sec", 600),
            api_key=cu_cfg.get("api_key", ""),
        )
        self._poll_sec = cu_cfg.get("poll_interval_sec", 2)
        self._checkpoint = cu_cfg["sd_checkpoint"]
        self._negative = cu_cfg.get("negative_prompt", "")
        self._steps = cu_cfg.get("steps", 20)
        self._cfg_scale = cu_cfg.get("cfg_scale", 7.0)
        self._sampler = cu_cfg.get("sampler", "euler")
        self._img_w = cu_cfg.get("image_width", 512)
        self._img_h = cu_cfg.get("image_height", 512)
        self._motion_module = ad_cfg.get("motion_module", "mm_sd_v15_v2.ckpt")
        self._frames = ad_cfg.get("frames", 16)
        self._fps = ad_cfg.get("fps", 8)
        self._context_length = ad_cfg.get("context_length", 16)
        self._skip_visual = config.get("skip_visual", False)

        self._temp_dir = Path(temp_dir)
        self._image_dir = self._temp_dir / "images"
        self._video_dir = self._temp_dir / "video"
        self._image_dir.mkdir(parents=True, exist_ok=True)
        self._video_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, scenes: list[Scene]) -> list[Scene]:
        """Generate video clips for all scenes. Sets scene.video_clip_path in-place."""
        for scene in tqdm(scenes, desc="Visual", unit="scene"):
            self._generate_scene(scene)
        return scenes

    # ── private ───────────────────────────────────────────────────────────────

    def _generate_scene(self, scene: Scene) -> None:
        out_path = self._video_dir / f"scene_{scene.scene_index:04d}.webp"
        if out_path.exists():
            scene.video_clip_path = str(out_path)
            return

        if self._skip_visual:
            # Create a styled placeholder image (will be looped by renderer)
            self._create_visual_placeholder(scene, out_path)
            scene.video_clip_path = str(out_path)
            return

        # Step 1: Generate SD image
        image_path = self._generate_sd_image(scene)

        # Step 2: AnimateDiff — real AI animation
        video_path = self._run_animatediff(scene, image_path)
        scene.video_clip_path = str(video_path)

    def _generate_sd_image(self, scene: Scene) -> Path:
        img_path = self._image_dir / f"scene_{scene.scene_index:04d}.png"
        if img_path.exists():
            return img_path

        wf = self._build_sd_workflow(scene.scene_prompt, f"scene_{scene.scene_index:04d}")
        prompt_id = self._client.queue_prompt(wf)
        outputs = self._client.wait_for_completion(prompt_id, self._poll_sec)

        # SaveImage node id=7 in our workflow
        images = outputs.get("7", {}).get("images", [])
        if not images:
            raise RuntimeError(f"SD generated no images for scene {scene.scene_index}")

        img_data = self._client.get_image_bytes(
            images[0]["filename"],
            images[0].get("subfolder", ""),
            images[0].get("type", "output"),
        )
        img_path.write_bytes(img_data)
        return img_path

    def _run_animatediff(self, scene: Scene, _image_path: Path) -> Path:
        out_path = self._video_dir / f"scene_{scene.scene_index:04d}.webp"
        wf = self._build_ad_workflow(scene.scene_prompt, f"scene_{scene.scene_index:04d}")
        prompt_id = self._client.queue_prompt(wf)
        outputs = self._client.wait_for_completion(prompt_id, self._poll_sec)

        # VHS_VideoCombine node id=13 in our workflow
        gifs = outputs.get("13", {}).get("gifs", [])
        if not gifs:
            raise RuntimeError(f"AnimateDiff generated no video for scene {scene.scene_index}")

        vid_data = self._client.get_video_bytes(
            gifs[0]["filename"],
            gifs[0].get("subfolder", ""),
            gifs[0].get("type", "output"),
        )
        out_path.write_bytes(vid_data)
        return out_path

    def _build_sd_workflow(self, prompt: str, prefix: str) -> dict:
        """Build ComfyUI API-format SD workflow dict."""
        import random
        seed = random.randint(0, 2**31)
        return {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self._checkpoint},
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["1", 1]},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": self._negative, "clip": ["1", 1]},
            },
            "4": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": self._img_w, "height": self._img_h, "batch_size": 1},
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0],
                    "seed": seed,
                    "steps": self._steps,
                    "cfg": self._cfg_scale,
                    "sampler_name": self._sampler,
                    "scheduler": "normal",
                    "denoise": 1.0,
                },
            },
            "6": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
            },
            "7": {
                "class_type": "SaveImage",
                "inputs": {"images": ["6", 0], "filename_prefix": prefix},
            },
        }

    def _build_ad_workflow(self, prompt: str, prefix: str) -> dict:
        """Build ComfyUI API-format AnimateDiff workflow dict."""
        import random
        seed = random.randint(0, 2**31)
        return {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self._checkpoint},
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["1", 1]},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": self._negative, "clip": ["1", 1]},
            },
            "10": {
                "class_type": "ADE_AnimateDiffLoaderWithContext",
                "inputs": {
                    "model": ["1", 0],
                    "model_name": self._motion_module,
                    "beta_schedule": "autoselect",
                    "motion_scale": 1.0,
                    "apply_v2_models_properly": True,
                },
            },
            "11": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": self._img_w,
                    "height": self._img_h,
                    "batch_size": self._frames,
                },
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["10", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["11", 0],
                    "seed": seed,
                    "steps": self._steps,
                    "cfg": self._cfg_scale,
                    "sampler_name": self._sampler,
                    "scheduler": "normal",
                    "denoise": 1.0,
                },
            },
            "6": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
            },
            "13": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["6", 0],
                    "frame_rate": self._fps,
                    "loop_count": 0,
                    "filename_prefix": prefix,
                    "format": "image/webp",
                    "pingpong": False,
                    "save_output": True,
                },
            },
        }

    @staticmethod
    def _create_visual_placeholder(scene: "Scene", out_path: Path) -> None:
        """Create a styled gradient image with scene info as placeholder visual."""
        try:
            import textwrap
            from PIL import Image, ImageDraw, ImageFont

            W, H = 1280, 720

            # Color palette per section type
            _PALETTES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
                "hook":              ((15, 5, 40),   (60, 15, 100),  (180, 50, 220)),
                "problem":          ((40, 5, 10),   (100, 15, 20),  (220, 50, 60)),
                "content":          ((5, 15, 45),   (15, 50, 100),  (50, 150, 220)),
                "solution":         ((5, 35, 20),   (15, 90, 50),   (50, 200, 120)),
                "cta":              ((45, 30, 5),   (120, 80, 10),  (240, 180, 30)),
                "pattern_interrupt":((5, 40, 45),   (10, 120, 140), (30, 220, 240)),
                "teaser":           ((30, 5, 45),   (80, 15, 120),  (180, 60, 230)),
            }
            dark, mid, accent = _PALETTES.get(
                scene.section_type, ((10, 10, 30), (30, 30, 80), (80, 100, 200))
            )

            # Draw vertical gradient background
            img = Image.new("RGB", (W, H), dark)
            draw = ImageDraw.Draw(img)
            for y in range(H):
                t = y / H
                r = int(dark[0] + (mid[0] - dark[0]) * t)
                g = int(dark[1] + (mid[1] - dark[1]) * t)
                b = int(dark[2] + (mid[2] - dark[2]) * t)
                draw.line([(0, y), (W, y)], fill=(r, g, b))

            # Decorative top bar
            draw.rectangle([(0, 0), (W, 8)], fill=accent)
            draw.rectangle([(0, H - 8), (W, H)], fill=accent)

            # Corner decorative marks
            for x, y in [(20, 20), (W - 20, 20), (20, H - 20), (W - 20, H - 20)]:
                draw.rectangle([(x - 8, y - 2), (x + 8, y + 2)], fill=accent)
                draw.rectangle([(x - 2, y - 8), (x + 2, y + 8)], fill=accent)

            # Try loading a font; fall back gracefully
            try:
                font_label = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 28)
                font_prompt = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 32)
                font_index = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 22)
            except OSError:
                font_label = font_prompt = font_index = ImageFont.load_default()

            # Scene type label (top-left)
            label = scene.section_type.upper().replace("_", " ")
            draw.text((32, 24), label, font=font_label, fill=accent)

            # Scene index badge (top-right)
            badge = f"SCENE {scene.scene_index + 1}"
            draw.text((W - 160, 24), badge, font=font_index, fill=accent)

            # Scene prompt text (centered, word-wrapped)
            prompt_text = scene.scene_prompt or scene.text or ""
            max_chars = 55
            lines = textwrap.wrap(prompt_text, width=max_chars)[:6]
            line_h = 44
            total_h = len(lines) * line_h
            start_y = (H - total_h) // 2

            for i, line in enumerate(lines):
                # Shadow
                draw.text((W // 2 - 1 + 2, start_y + i * line_h + 2), line,
                          font=font_prompt, fill=(0, 0, 0), anchor="mm")
                # Main text
                draw.text((W // 2, start_y + i * line_h), line,
                          font=font_prompt, fill=(240, 240, 255), anchor="mm")

            # Horizontal divider above/below text
            pad = 20
            draw.rectangle([(80, start_y - pad - 3), (W - 80, start_y - pad)], fill=accent)
            draw.rectangle([(80, start_y + total_h + pad), (W - 80, start_y + total_h + pad + 3)], fill=accent)

            img.save(str(out_path), format="WEBP", quality=90)

        except Exception:
            # Absolute fallback: dark navy instead of pure black
            from PIL import Image
            img = Image.new("RGB", (1280, 720), (10, 10, 40))
            img.save(str(out_path), format="WEBP")
