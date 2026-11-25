"""Gemini API (google-genai): ER 2 and the Gemini generalist. Key: GEMINI_API_KEY."""

from __future__ import annotations

from pathlib import Path

from .base import Adapter, Reply


class Gemini(Adapter):
    def __init__(self, model_id: str, thinking_budget: int | None = None, client=None):
        self.model_id, self.thinking_budget = model_id, thinking_budget
        from google.genai import types  # imported here so mock runs and tests need no SDK

        self._types = types
        if client is None:
            from google import genai

            client = genai.Client()
        self.client = client

    def ask(self, system, parts, *, task, condition, run):
        t = self._types
        contents = [t.Part.from_bytes(data=Path(v).read_bytes(), mime_type="image/png") if k == "image" else v
                    for k, v in parts]
        config = t.GenerateContentConfig(system_instruction=system)
        if self.thinking_budget is not None:
            config.thinking_config = t.ThinkingConfig(thinking_budget=self.thinking_budget)
        resp = self.client.models.generate_content(model=self.model_id, contents=contents, config=config)
        u = resp.usage_metadata
        # thinking tokens are billed as output (handoff: ER 2 output price includes thinking)
        out = (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
        return Reply(text=resp.text or "", tokens_in=getattr(u, "prompt_token_count", 0) or 0, tokens_out=out)
