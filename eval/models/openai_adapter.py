"""OpenAI Responses API. Key: OPENAI_API_KEY."""

from __future__ import annotations

import base64
from pathlib import Path

from .base import Adapter, Reply


class OpenAI(Adapter):
    def __init__(self, model_id: str, client=None, **_):
        self.model_id = model_id
        if client is None:
            import openai

            client = openai.OpenAI()
        self.client = client

    def ask(self, system, parts, *, task, condition, run):
        content = [{"type": "input_image",
                    "image_url": "data:image/png;base64," + base64.b64encode(Path(v).read_bytes()).decode()}
                   if k == "image" else {"type": "input_text", "text": v} for k, v in parts]
        resp = self.client.responses.create(model=self.model_id, instructions=system,
                                            input=[{"role": "user", "content": content}])
        u = resp.usage  # output_tokens includes reasoning tokens
        return Reply(text=resp.output_text or "", tokens_in=u.input_tokens, tokens_out=u.output_tokens)
