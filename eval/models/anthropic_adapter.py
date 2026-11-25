"""Anthropic Messages API. Key: ANTHROPIC_API_KEY."""

from __future__ import annotations

import base64
from pathlib import Path

from .base import Adapter, Reply

MAX_TOKENS = 2048  # answers are short JSON; headroom for any reasoning preamble


class Anthropic(Adapter):
    def __init__(self, model_id: str, client=None, **_):
        self.model_id = model_id
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def ask(self, system, parts, *, task, condition, run):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                "data": base64.b64encode(Path(v).read_bytes()).decode()}}
                   if k == "image" else {"type": "text", "text": v} for k, v in parts]
        resp = self.client.messages.create(model=self.model_id, max_tokens=MAX_TOKENS, system=system,
                                           messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return Reply(text=text, tokens_in=resp.usage.input_tokens, tokens_out=resp.usage.output_tokens)
