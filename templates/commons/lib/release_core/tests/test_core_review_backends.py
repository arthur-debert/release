"""Tests for the single-repo review backends (Phase 1.1).

No test invokes the real codex/agy binaries — those are intentionally not
running. We exercise the pure surfaces: prompt parity across backends, the
preflight probe (monkeypatched), the three-fallback JSON extractor, and the
build_command descriptions.
"""

from __future__ import annotations

import importlib
import json

import pytest
from release_core.review import prompt as prompt_mod
from release_core.review.backends import AgyBackend, CodexBackend, get_backend
from release_core.review.backends.base import (
    Backend,
    BackendError,
    BackendUnavailable,
    parse_review_output,
)
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


# --- parse_review_output: the backend-boundary wrapper -----------------------
#
# extract_json stays as-is (bare ValueError); parse_review_output wraps it so a
# parse failure raises a CLEAN BackendError (a RuntimeError subclass that
# `_LocalReviewAdapter.request` already normalizes to GhError — no traceback).


def test_parse_review_output_passes_valid_json_through():
    assert parse_review_output('{"a": 1}') == {"a": 1}


def test_parse_review_output_raises_backenderror_not_valueerror():
    with pytest.raises(BackendError) as exc:
        parse_review_output("not json at all")
    # BackendError subclasses RuntimeError (so request() catches it), and it is
    # NOT a bare ValueError leaking from extract_json.
    assert isinstance(exc.value, RuntimeError)
    assert not isinstance(exc.value, ValueError)
    # The original ValueError is preserved as the cause.
    assert isinstance(exc.value.__cause__, ValueError)


def test_parse_review_output_includes_a_snippet_for_debugging():
    junk = "x" * 1000
    with pytest.raises(BackendError) as exc:
        parse_review_output(junk)
    msg = str(exc.value)
    assert "raw output" in msg
    assert "xxx" in msg  # a slice of the raw output is echoed back


def test_parse_review_output_names_the_timeout_when_marker_present():
    # The live agy failure: a TRUNCATED JSON object then agy's timeout marker.
    truncated = (
        '{"summary": {"status": "COMMENT", "overall_fee\nError: timed out waiting for response'
    )
    with pytest.raises(BackendError) as exc:
        parse_review_output(truncated)
    msg = str(exc.value).lower()
    assert "timed out" in msg
    # actionable hint: a faster model or a smaller diff.
    assert "faster model" in msg or "smaller diff" in msg


def test_parse_review_output_timeout_marker_is_case_insensitive():
    with pytest.raises(BackendError) as exc:
        parse_review_output("{trunc\nTIMED OUT WAITING FOR RESPONSE")
    assert "timed out" in str(exc.value).lower()


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


def test_agy_argv_carries_a_ten_minute_print_timeout():
    # agy's `--print` timeout defaults to 5m; a large review can exceed it and
    # return a truncated JSON + timeout marker. The argv must give 10m headroom.
    cmd = AgyBackend().build_command("prompt body", REVIEW_SCHEMA)
    assert "--print-timeout=600s" in cmd["argv"]


# --- run(): a bad/truncated agent output fails cleanly, never a raw traceback --


def _completed(stdout: str):
    import subprocess

    return subprocess.CompletedProcess(args=["x"], returncode=0, stdout=stdout, stderr="")


def _backend_module(backend):
    # Each backend references `proc` imported into ITS module namespace
    # (`from ... import proc`), so monkeypatch must target that module.
    return importlib.import_module(backend.__module__)


@pytest.mark.parametrize("backend", [CodexBackend(), AgyBackend()])
def test_run_raises_backenderror_on_unparseable_output(monkeypatch, backend):
    # The backend ran but returned junk (not JSON): run() must raise the clean
    # BackendError, NOT a bare ValueError escaping from extract_json.
    monkeypatch.setattr(
        _backend_module(backend).proc, "run", lambda *a, **k: _completed("this is not json")
    )
    with pytest.raises(BackendError):
        backend.run("prompt", REVIEW_SCHEMA)


@pytest.mark.parametrize("backend", [CodexBackend(), AgyBackend()])
def test_run_names_timeout_on_truncated_output_with_marker(monkeypatch, backend):
    truncated = '{"summary": {"status": "COMM\nError: timed out waiting for response'
    monkeypatch.setattr(_backend_module(backend).proc, "run", lambda *a, **k: _completed(truncated))
    with pytest.raises(BackendError) as exc:
        backend.run("prompt", REVIEW_SCHEMA)
    assert "timed out" in str(exc.value).lower()


@pytest.mark.parametrize("backend", [CodexBackend(), AgyBackend()])
def test_run_returns_parsed_review_on_good_output(monkeypatch, backend):
    good = '{"summary": {"status": "APPROVED", "overall_feedback": "ok"}, "comments": []}'
    monkeypatch.setattr(_backend_module(backend).proc, "run", lambda *a, **k: _completed(good))
    parsed = backend.run("prompt", REVIEW_SCHEMA)
    assert parsed["summary"]["status"] == "APPROVED"


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
