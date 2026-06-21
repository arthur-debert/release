"""Tests for the single-repo review backends (Phase 1.1).

No test invokes the real codex/agy binaries — those are intentionally not
running. We exercise the pure surfaces: prompt parity across backends, the
preflight probe (monkeypatched), the three-fallback JSON extractor, and the
build_command descriptions.
"""

from __future__ import annotations

import json

import pytest
from release_core.review import prompt as prompt_mod
from release_core.review.backends import AgyBackend, CodexBackend, get_backend
from release_core.review.backends.base import Backend, BackendUnavailable
from release_core.review.schema import REVIEW_SCHEMA, extract_json

INSTRUCTIONS = "Review rigorously. Flag bugs and missing tests."
DIFF = "diff --git a/x.py b/x.py\n@@ -1 +1 @@\n-old\n+new\n"


# --- parity: the shared prompt body is identical across backends -------------


def test_build_prompt_embeds_instructions_and_diff():
    body = prompt_mod.build_prompt(INSTRUCTIONS, DIFF, schema_inline=False)
    assert INSTRUCTIONS in body
    assert DIFF in body


def test_schema_inline_only_adds_prose():
    plain = prompt_mod.build_prompt(INSTRUCTIONS, DIFF, schema_inline=False)
    inline = prompt_mod.build_prompt(INSTRUCTIONS, DIFF, schema_inline=True)
    # The inline variant is the plain body plus an appended schema description.
    assert inline.startswith(plain)
    assert "JSON Schema" in inline
    assert "JSON Schema" not in plain


def test_backends_embed_the_same_prompt_body():
    """The semantic payload sent to each agent must match: codex carries the
    shared prompt in stdin, agy carries it in the temp prompt file."""
    # Each backend gets the prompt flavour it expects (codex enforces the schema
    # natively, agy needs it in-prose) — but the SHARED body is identical.
    codex_prompt = prompt_mod.build_prompt(INSTRUCTIONS, DIFF, schema_inline=False)
    agy_prompt = prompt_mod.build_prompt(INSTRUCTIONS, DIFF, schema_inline=True)

    codex_cmd = CodexBackend().build_command(codex_prompt, REVIEW_SCHEMA)
    agy_cmd = AgyBackend().build_command(agy_prompt, REVIEW_SCHEMA)

    # codex: prompt rides on stdin.
    assert codex_cmd["stdin"] == codex_prompt
    # agy: prompt rides in the (single) temp file.
    assert list(agy_cmd["files"].values()) == [agy_prompt]

    # The shared body (instructions + diff) is byte-identical in both payloads.
    shared = prompt_mod.build_prompt(INSTRUCTIONS, DIFF, schema_inline=False)
    agy_file_contents = next(iter(agy_cmd["files"].values()))
    assert shared in codex_cmd["stdin"]
    assert shared in agy_file_contents


# --- preflight ----------------------------------------------------------------


@pytest.mark.parametrize("backend", [CodexBackend(), AgyBackend()])
def test_preflight_raises_when_binary_missing(monkeypatch, backend):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(BackendUnavailable) as exc:
        backend.preflight()
    msg = str(exc.value)
    assert backend.binary in msg
    assert "PATH" in msg or "Install" in msg


@pytest.mark.parametrize("backend", [CodexBackend(), AgyBackend()])
def test_preflight_passes_when_binary_present(monkeypatch, backend):
    monkeypatch.setattr("shutil.which", lambda _name: f"/usr/bin/{backend.binary}")
    backend.preflight()  # must not raise


# --- extract_json: the three fallback paths ----------------------------------


def test_extract_json_direct():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_fences():
    text = '```json\n{"a": 1, "b": [2, 3]}\n```'
    assert extract_json(text) == {"a": 1, "b": [2, 3]}


def test_extract_json_regex_fallback():
    text = 'Here is your review:\n{"a": 1}\nThanks!'
    assert extract_json(text) == {"a": 1}


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("no json here at all")


# --- build_command shape ------------------------------------------------------


def test_codex_build_command_shape():
    prompt = prompt_mod.build_prompt(INSTRUCTIONS, DIFF, schema_inline=False)
    cmd = CodexBackend().build_command(prompt, REVIEW_SCHEMA)
    argv = cmd["argv"]
    joined = " ".join(argv)
    assert "exec" in argv
    assert "--sandbox" in argv and "read-only" in argv
    assert "--output-schema" in argv
    assert cmd["stdin"] == prompt
    # The single temp file is the schema, written as JSON.
    assert len(cmd["files"]) == 1
    schema_contents = next(iter(cmd["files"].values()))
    assert json.loads(schema_contents) == REVIEW_SCHEMA
    assert "--output-schema" in joined


def test_agy_build_command_shape():
    prompt = prompt_mod.build_prompt(INSTRUCTIONS, DIFF, schema_inline=True)
    cmd = AgyBackend().build_command(prompt, REVIEW_SCHEMA)
    argv = cmd["argv"]
    assert "--print" in argv
    assert cmd["stdin"] is None
    # The prompt rides in a temp file, not on the argv/stdin.
    assert len(cmd["files"]) == 1
    assert next(iter(cmd["files"].values())) == prompt


def test_codex_model_aliases():
    assert CodexBackend("pro").model == "gpt-5.5"
    assert CodexBackend("flash").model == "gpt-5.4-mini"
    assert CodexBackend("flash_lite").model == "gpt-5.4-mini"
    # Unknown alias passes through unchanged.
    assert CodexBackend("gpt-5.5").model == "gpt-5.5"


def test_agy_model_aliases():
    # The default `pro` must NOT resolve to a bare "pro" (which agy silently maps
    # to an agentic Gemini 3.5 Flash that never returns JSON) — it pins to Pro.
    assert AgyBackend("pro").model == "Gemini 3.1 Pro (High)"
    assert AgyBackend("flash").model == "Gemini 3.5 Flash (High)"
    assert AgyBackend("flash_lite").model == "Gemini 3.5 Flash (Low)"
    # An explicit verbatim model name passes through unchanged.
    assert AgyBackend("Claude Opus 4.6 (Thinking)").model == "Claude Opus 4.6 (Thinking)"
    assert AgyBackend("Gemini 3.1 Pro (High)").model == "Gemini 3.1 Pro (High)"


def test_agy_default_model_resolves_in_argv():
    # The resolved model lands in a single `--model=<resolved>` argv element.
    cmd = AgyBackend().build_command("prompt body", REVIEW_SCHEMA)
    assert "--model=Gemini 3.1 Pro (High)" in cmd["argv"]


# --- registry -----------------------------------------------------------------


def test_get_backend_returns_instances():
    assert isinstance(get_backend("codex"), CodexBackend)
    assert isinstance(get_backend("agy"), AgyBackend)
    assert isinstance(get_backend("codex"), Backend)


def test_get_backend_forwards_kwargs():
    assert get_backend("codex", model="flash").model == "gpt-5.4-mini"


def test_get_backend_unknown_raises():
    with pytest.raises(ValueError):
        get_backend("nope")


# --- single-repo schema invariant --------------------------------------------


def test_schema_has_no_repository_field():
    comment_props = REVIEW_SCHEMA["properties"]["comments"]["items"]["properties"]
    assert "repository" not in comment_props
    assert set(REVIEW_SCHEMA["properties"]) == {"summary", "comments"}


def test_default_instructions_wording():
    from release_core.review import instructions

    text = instructions.default_instructions()
    # The measurement section says we are UPHOLDING quality (not withholding).
    assert "upholding the quality" in text
    assert "withholding the quality" not in text
