"""Adapters against fake SDK clients: request shape, token accounting, retries. No network, no keys."""

import base64
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from eval import prompt
from eval.__main__ import MAX_TRIES, ask_with_retry, load_tasks, retryable
from eval.models.base import Adapter, Reply

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
TASK = load_tasks(FIXTURES / "tasks.jsonl")[0]
F10 = next(t for t in load_tasks(FIXTURES / "tasks.jsonl") if t["family"] == "F10")
PNG = (FIXTURES / TASK["images"][0]).read_bytes()


class Recorder:
    """Stands in for client.models / client.messages / client.responses."""

    def __init__(self, reply):
        self.reply, self.kw = reply, None

    def __call__(self, **kw):
        self.kw = kw
        return self.reply


def test_anthropic_request_and_tokens():
    from eval.models.anthropic_adapter import Anthropic

    rec = Recorder(NS(content=[NS(type="text", text='{"point": [1, 2]}')], usage=NS(input_tokens=1500, output_tokens=9)))
    a = Anthropic("claude-x", client=NS(messages=NS(create=rec)))
    r = a.ask(*prompt.build(TASK, FIXTURES, "image"), task=TASK, condition="image", run=0)
    assert r == Reply('{"point": [1, 2]}', 1500, 9)
    assert rec.kw["model"] == "claude-x" and rec.kw["system"] == prompt.SYSTEM
    img, text = rec.kw["messages"][0]["content"]
    assert img["type"] == "image" and base64.b64decode(img["source"]["data"]) == PNG
    assert TASK["question"] in text["text"]


def test_openai_request_and_tokens():
    from eval.models.openai_adapter import OpenAI

    rec = Recorder(NS(output_text='{"count": 2}', usage=NS(input_tokens=900, output_tokens=40)))
    o = OpenAI("gpt-x", client=NS(responses=NS(create=rec)))
    r = o.ask(*prompt.build(F10, FIXTURES, "image"), task=F10, condition="image", run=0)
    assert r == Reply('{"count": 2}', 900, 40)
    content = rec.kw["input"][0]["content"]
    assert [c["type"] for c in content] == ["input_text", "input_image", "input_text", "input_image", "input_text"]
    assert content[1]["image_url"].startswith("data:image/png;base64,") and rec.kw["instructions"] == prompt.SYSTEM


def test_gemini_request_tokens_and_thinking_budget():
    pytest.importorskip("google.genai")
    from eval.models.gemini_adapter import Gemini

    usage = NS(prompt_token_count=1300, candidates_token_count=10, thoughts_token_count=200)
    rec = Recorder(NS(text='{"bool": true}', usage_metadata=usage))
    g = Gemini("gemini-robotics-er-2-preview", thinking_budget=0, client=NS(models=NS(generate_content=rec)))
    r = g.ask(*prompt.build(TASK, FIXTURES, "text_only"), task=TASK, condition="text_only", run=0)
    assert r == Reply('{"bool": true}', 1300, 210)                 # thinking billed as output
    cfg = rec.kw["config"]
    assert cfg.system_instruction == prompt.SYSTEM and cfg.thinking_config.thinking_budget == 0
    assert all(isinstance(c, str) for c in rec.kw["contents"])     # text_only: no image parts
    g2 = Gemini("x", client=NS(models=NS(generate_content=rec)))
    g2.ask(*prompt.build(TASK, FIXTURES, "image"), task=TASK, condition="image", run=0)
    assert rec.kw["config"].thinking_config is None                # default: leave it to the API
    assert rec.kw["contents"][0].inline_data.data == PNG


class Flaky(Adapter):
    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0

    def ask(self, *a, **kw):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return Reply("ok")


def err(name, code=None):
    e = type(name, (Exception,), {})("boom")
    if code:
        e.status_code = code
    return e


def test_retries_transient_errors_then_succeeds():
    m, slept = Flaky([err("RateLimitError", 429), err("APITimeoutError"), err("InternalServerError", 503)]), []
    assert ask_with_retry(m, sleep=slept.append).text == "ok"
    assert m.calls == 4 and len(slept) == 3 and slept[0] < slept[1] < slept[2]


def test_gives_up_after_max_tries():
    m = Flaky([err("RateLimitError", 429)] * MAX_TRIES)
    with pytest.raises(Exception, match="boom"):
        ask_with_retry(m, sleep=lambda s: None)
    assert m.calls == MAX_TRIES


@pytest.mark.parametrize("e", [err("AuthenticationError", 401), err("BadRequestError", 400), err("NotFoundError", 404)])
def test_permanent_errors_fail_at_once(e):
    m = Flaky([e])
    with pytest.raises(Exception):
        ask_with_retry(m, sleep=lambda s: None)
    assert m.calls == 1 and not retryable(e)
