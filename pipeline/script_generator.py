"""Script generator: calls Ollama to produce a structured JSON script."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, field_validator

# ── Pydantic models ───────────────────────────────────────────────────────────

class SectionSchema(BaseModel):
    id: str
    type: str  # hook | problem | content | solution | cta
    narrator: str
    pattern_interrupt: Optional[str] = None
    teaser: Optional[str] = None
    scene_prompt: str

    @field_validator("narrator")
    @classmethod
    def narrator_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("narrator must not be empty")
        return v.strip()

    @field_validator("scene_prompt")
    @classmethod
    def prompt_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("scene_prompt must not be empty")
        return v.strip()


class ScriptSchema(BaseModel):
    title: str
    sections: list[SectionSchema]

    @field_validator("sections")
    @classmethod
    def at_least_four_sections(cls, v: list) -> list:
        if len(v) < 4:
            raise ValueError("Script must have at least 4 sections")
        return v


# ── Generator ────────────────────────────────────────────────────────────────

class ScriptGenerator:
    def __init__(self, config: dict) -> None:
        self._cfg = config["ollama"]
        self._system_prompt = (
            Path("prompts/script_system.txt").read_text(encoding="utf-8")
        )
        self._user_template = (
            Path("prompts/script_user.txt").read_text(encoding="utf-8")
        )

    def generate(self, topic: str) -> ScriptSchema:
        """Call Ollama and return a validated ScriptSchema."""
        user_msg = self._user_template.format(topic=topic)
        payload = {
            "model": self._cfg["model"],
            "prompt": f"{self._system_prompt}\n\n{user_msg}",
            "stream": True,
            "options": {
                "temperature": self._cfg.get("temperature", 0.7),
                "num_predict": self._cfg.get("max_tokens", 4096),
            },
        }
        timeout = httpx.Timeout(
            connect=10.0,
            read=self._cfg.get("timeout_sec", 300),
            write=10.0,
            pool=10.0,
        )
        chunks: list[str] = []
        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "POST",
                f"{self._cfg['base_url']}/api/generate",
                json=payload,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunks.append(data.get("response", ""))
                    if data.get("done", False):
                        break

        raw_text = "".join(chunks)
        return self._parse_response(raw_text)

    # ── private ──────────────────────────────────────────────────────────────

    def _parse_response(self, raw: str) -> ScriptSchema:
        """Extract JSON from LLM response (handles markdown code fences)."""
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

        # Find the outermost JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError(f"No JSON object found in LLM response:\n{raw[:500]}")

        json_str = cleaned[start:end]
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"JSON parse error: {exc}\nRaw snippet:\n{json_str[:500]}"
            ) from exc

        return ScriptSchema(**data)
