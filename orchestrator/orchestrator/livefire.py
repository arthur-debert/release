"""orc livefire — the live-fire verification harness runner (release#663).

Single-consumer path: clone a consumer, run the ONE standard live-fire prompt
(``docs/dev/live-fire-prompt.md``) via a fresh subordinate agent
(``bypassPermissions`` in a throwaway clone that pushes a REAL coverage PR to
the consumer's origin), harvest the structured YAML feedback the agent emits,
file each finding into the release#348 inbox (``release-core issue file``), and
tear down the throwaway ``-release-rc`` tag + GitHub pre-release.

Parallel rollout across N consumers is a follow-up (#663.3 phase 2); the pure
pieces here (prompt load, feedback parse, finding→issue mapping, teardown
command) are written stand-alone so that fan-out is just an ``asyncio.gather``
over :func:`livefire_one`.

The pure functions (everything except :func:`livefire_one`) take no I/O state
and are unit-tested without the SDK or network.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .boot import BootError, boot_clone

# NOTE: `yaml` (parse_feedback) and `.session`/the Claude Agent SDK
# (livefire_one) are imported LAZILY inside the functions that use them — this
# module must stay importable WITHOUT the SDK or pyyaml so its pure logic is
# collected by the light, SDK-free `pytest` CI job (same contract as
# orchestrator.watch; see pyproject testpaths note, release#578).

PROMPT_DOC_REL = "docs/dev/live-fire-prompt.md"
# The agent prompt lives in a 4-backtick ```` ```text ```` fence so the embedded
# 3-backtick ```yaml schema example survives verbatim. Extract between them.
_PROMPT_FENCE_RE = re.compile(r"^````text$", re.MULTILINE)
_YAML_BLOCK_RE = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.DOTALL)
# A strict verification-tag shape — vX.Y.Z-release-rc (optional .N) — so a
# transcript-sourced tag can't trigger a destructive delete unless it is exactly
# a reserved verification tag (release#663).
_VERIFY_TAG_RE = re.compile(r"v\d+\.\d+\.\d+-release-rc(\.\d+)?")


class LiveFireError(RuntimeError):
    """A live-fire run failed in a way the operator must see (no silent pass)."""


# ── Pure helpers (unit-tested, no I/O) ────────────────────────────────────


def release_root() -> Path:
    """The release repo root. Prefer $RELEASE_HOME; else derive from this file
    (``<root>/orchestrator/orchestrator/livefire.py`` → parents[2])."""
    home = os.environ.get("RELEASE_HOME")
    if home:
        return Path(home).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def load_prompt(doc_path: str | Path | None = None) -> str:
    """Return the verbatim agent prompt — the body of the ```` ```text ```` fence
    in ``live-fire-prompt.md`` (the single source, shared with the human doc)."""
    path = Path(doc_path) if doc_path else release_root() / PROMPT_DOC_REL
    text = path.read_text(encoding="utf-8")
    m = _PROMPT_FENCE_RE.search(text)
    if not m:
        raise LiveFireError(f"no ````text prompt fence in {path}")
    after = text[m.end() :].lstrip("\n")
    # The fence closes on a line that is exactly ```` (4 backticks).
    end = re.search(r"^````\s*$", after, re.MULTILINE)
    if not end:
        raise LiveFireError(f"unterminated ````text fence in {path}")
    body = after[: end.start()].rstrip("\n")
    if not body.strip():
        raise LiveFireError(f"empty prompt fence in {path}")
    return body


def parse_feedback(transcript: str) -> dict:
    """Extract + parse the LAST ```yaml block from the agent transcript.

    The prompt instructs the agent to END with the feedback block, so the last
    yaml fence is the report (earlier ones may be incidental). Raises rather
    than returning a partial — a missing/unparseable report is a failed run.
    """
    import yaml  # lazy — keep module import SDK/dep-free for the light CI job

    blocks = _YAML_BLOCK_RE.findall(transcript)
    if not blocks:
        raise LiveFireError("agent produced no ```yaml feedback block")
    try:
        data = yaml.safe_load(blocks[-1])
    except yaml.YAMLError as e:
        raise LiveFireError(f"feedback block is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise LiveFireError("feedback block did not parse to a mapping")
    return data


_SYMPTOM_MAX = 200


def findings_to_issues(feedback: dict, *, consumer: str) -> list[dict]:
    """Map non-``ok`` findings to inbox issue specs.

    ``severity: ok`` entries are dropped (signal, not noise — per the prompt's
    routing note). Each spec is ``{component, symptom}`` ready for
    ``release-core issue file <component> <one-line-symptom>``. The ``issue
    file`` verb builds the ``[component]`` title + body itself, so ``symptom``
    is a single line WITHOUT a component prefix — the finding's detail
    (step/severity/what/expected) folded into one line and length-bounded.
    """
    specs: list[dict] = []
    for f in feedback.get("findings") or []:
        if not isinstance(f, dict):
            continue
        severity = str(f.get("severity") or "").strip().lower()
        if severity in ("", "ok"):
            continue
        component = str(f.get("component") or "uncategorized").strip() or "uncategorized"
        step = str(f.get("step") or "?").strip()
        what = str(f.get("what") or "").strip()
        expected = str(f.get("expected") or "").strip()
        symptom = f"live-fire/{step} ({severity}) on {consumer}: {what or '(no detail)'}"
        if expected:
            symptom += f" — expected: {expected}"
        # Collapse any newlines/runs of whitespace to keep it a single line, then
        # bound the length (it becomes the GitHub issue title).
        symptom = " ".join(symptom.split())
        if len(symptom) > _SYMPTOM_MAX:
            symptom = symptom[: _SYMPTOM_MAX - 1].rstrip() + "…"
        specs.append({"component": component, "symptom": symptom})
    return specs


def teardown_command(consumer: str, rc_tag: str | None) -> list[str] | None:
    """The gh command that deletes the throwaway rc release + tag, or None when
    there is nothing to tear down (blocked run / no rc cut).

    ``rc_tag`` comes from the agent transcript, so it is validated against a
    STRICT verification-tag shape (``vX.Y.Z-release-rc`` with an optional
    ``.N``) before constructing a destructive ``gh release delete``. A
    hallucinated or malformed value — even one that merely contains
    "release-rc" — yields None rather than risk deleting an unintended release
    on the consumer.
    """
    if not rc_tag:
        return None
    tag = rc_tag.strip()
    if not _VERIFY_TAG_RE.fullmatch(tag):
        return None
    return ["gh", "release", "delete", tag, "--repo", consumer, "--yes", "--cleanup-tag"]


# ── I/O steps ─────────────────────────────────────────────────────────────


def file_findings(feedback: dict, *, consumer: str, dry_run: bool = False) -> list[str]:
    """File each non-ok finding into the release#348 inbox via
    ``release-core issue file``. Returns the one-line symptoms filed (or that
    would be).

    Filing is part of the harness contract — silently dropping a finding defeats
    the self-improving loop — so a failed ``issue file`` RAISES after attempting
    all of them (every successful one still lands; the operator re-files the rest).
    """
    specs = findings_to_issues(feedback, consumer=consumer)
    filed: list[str] = []
    failures: list[str] = []
    for spec in specs:
        cmd = ["release-core", "issue", "file", spec["component"], spec["symptom"]]
        if dry_run:
            print(f"[dry-run] would file: [{spec['component']}] {spec['symptom']}", file=sys.stderr)
        else:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                failures.append(f"[{spec['component']}] {spec['symptom']}: {res.stderr.strip()}")
                continue
        filed.append(spec["symptom"])
    if failures:
        raise LiveFireError(
            f"failed to file {len(failures)} of {len(specs)} finding(s) to the #348 "
            "inbox (re-file by hand): " + "; ".join(failures)
        )
    return filed


def teardown_rc(consumer: str, rc_tag: str | None, *, dry_run: bool = False) -> str | None:
    """Delete the throwaway rc tag + GH pre-release on the consumer. Returns the
    tag torn down, or None if there was nothing to do.

    Teardown is part of the harness contract: a failed delete leaves a stray
    ``-release-rc`` release/tag on the consumer, so it RAISES (fails the run)
    rather than warning — the operator must clean it up immediately.
    """
    cmd = teardown_command(consumer, rc_tag)
    if cmd is None:
        return None
    if dry_run:
        print(f"[dry-run] would teardown: {' '.join(cmd)}", file=sys.stderr)
        return rc_tag
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise LiveFireError(
            f"rc teardown failed for {rc_tag} on {consumer} "
            f"(stray pre-release left behind — delete it by hand): {res.stderr.strip()}"
        )
    return rc_tag


def _clone(consumer: str, parent: Path) -> Path:
    """Clone the consumer from GitHub into a throwaway dir (origin = the real
    remote, so the agent's coverage PR is real). Uses `gh repo clone` for auth."""
    dest = parent / consumer.split("/")[-1]
    res = subprocess.run(
        ["gh", "repo", "clone", consumer, str(dest)], capture_output=True, text=True
    )
    if res.returncode != 0:
        raise LiveFireError(f"clone of {consumer} failed: {res.stderr.strip()}")
    return dest


# ── Orchestration (single consumer) ───────────────────────────────────────


async def livefire_one(
    consumer: str,
    *,
    clone_parent: str | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    keep_clone: bool = False,
) -> dict:
    """Run the full single-consumer live-fire loop and return a summary dict.

    `consumer` is ``owner/name``. `dry_run` skips the *side-effecting* filing
    and teardown (the agent run still happens — that IS the test). Cloning into
    a fresh temp dir bounds the blast radius; the coverage PR it opens on the
    consumer's origin is real (the value left behind), the rc is torn down.

    The throwaway clone is removed when the run finishes (its only value was the
    pushed PR). Pass ``keep_clone=True`` (or an explicit ``clone_parent``) to
    retain it for debugging — a caller-supplied ``clone_parent`` is never
    deleted, since the caller owns it.
    """
    if clone_parent:
        parent, owns_parent = Path(clone_parent), False
    else:
        parent, owns_parent = Path(tempfile.mkdtemp(prefix="livefire-")), True

    try:
        clone = _clone(consumer, parent)

        try:
            boot_clone(str(clone))
        except BootError as e:
            raise LiveFireError(f"boot failed for {consumer} — run invalidated: {e}") from e

        from .session import run_session  # lazy — pulls the Claude Agent SDK

        prompt = load_prompt()
        sink: list[str] = []
        await run_session(
            str(clone),
            prompt,
            permission_mode="bypassPermissions",
            persist_session=False,
            verbose=verbose,
            text_sink=sink,
        )
        transcript = "".join(sink)
        feedback = parse_feedback(transcript)

        filed = file_findings(feedback, consumer=consumer, dry_run=dry_run)
        rc_tag = feedback.get("rc")
        torn_down = teardown_rc(
            consumer, rc_tag if isinstance(rc_tag, str) else None, dry_run=dry_run
        )

        retained = keep_clone or not owns_parent
        return {
            "consumer": consumer,
            "verdict": feedback.get("verdict"),
            "pr": feedback.get("pr"),
            "rc": rc_tag,
            "findings_filed": filed,
            "rc_torn_down": torn_down,
            # Only report a path that still exists; the default deletes the clone.
            "clone": str(clone) if retained else None,
        }
    finally:
        if owns_parent and not keep_clone:
            shutil.rmtree(parent, ignore_errors=True)
