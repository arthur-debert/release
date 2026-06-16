"""orc livefire — the live-fire verification harness runner (release#663).

Per consumer (:func:`livefire_one`): clone it, run the ONE standard live-fire
prompt (``docs/dev/live-fire-prompt.md``) via a fresh subordinate agent
(``bypassPermissions`` in a throwaway clone that pushes a REAL coverage PR to
the consumer's origin), harvest the structured YAML feedback the agent emits,
file each finding into the release#348 inbox (``release-core issue file``), and
tear down the throwaway ``-release-rc`` tag + GitHub pre-release.

Fleet rollout (:func:`livefire_many`): the same loop across N consumers
concurrently (``--all`` pulls the registry via ``release-core admin repos
list``), aggregated by :func:`summarize_rollout`. One consumer's failure is
captured in the report, never fatal.

The pure functions (everything except :func:`livefire_one` /
:func:`livefire_many`) take no I/O state and are unit-tested without the SDK or
network — the module stays importable without the Claude Agent SDK or pyyaml
(both lazy-imported) so the light CI ``pytest`` job collects it.
"""

from __future__ import annotations

import asyncio
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
# A line that is ONLY a code fence — ``` with an optional info string of any
# shape (`shell-session`, `c++`, `objective-c`, …), so we match anything after
# the backticks except a further backtick. A line starting with ``` is never
# valid YAML, so this only ever fires (and only on the salvage retry, after the
# raw block already failed to parse) on genuinely-stray fences.
_BARE_FENCE_RE = re.compile(r"^\s*```[^`]*$")
# A strict verification-tag shape — vX.Y.Z-release-rc (optional .N) — so a
# transcript-sourced tag can't trigger a destructive delete unless it is exactly
# a reserved verification tag (release#663).
# Boundary-guarded so an embedded token (e.g. `v1.2.3-release-rcXYZ`) can't
# yield a truncated `v1.2.3-release-rc` teardown target: the leading lookbehind
# requires a token start (not mid-word / mid-tag), and the trailing lookahead
# rejects a following word-char or `-` while still allowing sentence
# punctuation (a real `.N` is consumed by the optional group first).
_VERIFY_TAG_RE = re.compile(r"(?<![\w.-])v\d+\.\d+\.\d+-release-rc(\.\d+)?(?![\w-])")


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


def _strip_bare_fences(block: str) -> str:
    """Drop lines that are a bare ``` fence (optional surrounding whitespace).

    Agents routinely embed a code fence inside a feedback value (a snippet of
    the error they saw); an indented/mid-block ``` is then swept into the
    captured block by the non-greedy regex and makes the YAML loader choke on a
    stray backtick. A bare ``` line is never meaningful YAML, so dropping it
    salvages the common case without altering real content (caught live-firing
    rustloc — the agent fenced an example in its report)."""
    return "\n".join(ln for ln in block.splitlines() if not _BARE_FENCE_RE.match(ln))


# The follow-up prod when the agent finishes the task but skips the feedback
# block (release#683 — agents stop after the PR/rc on a long run). livefire_one
# passes this as run_session's `followup_prompt` with
# `needs_followup=lambda t: not has_feedback(t)`, so it's sent ONCE in the same
# session when the transcript still lacks a feedback block.
FOLLOWUP_FEEDBACK_PROMPT = (
    "You have not emitted the required feedback block. Output ONLY the single "
    "fenced ```yaml feedback block now (repo / verdict / pr / rc / findings, as "
    "specified) — nothing before or after it."
)


def _first_feedback(transcript: str) -> dict | None:
    """The agent's feedback mapping, or None if absent.

    Scans yaml blocks LAST-first (the report ends the response) and accepts only
    a mapping that carries a ``verdict`` key — the one mandatory field — so an
    INCIDENTAL yaml block (one the agent read from a managed file like
    dev-cycle.lex / a skill, which every consumer carries) is skipped rather
    than mis-parsed as the report (release#683). Tolerates a stray embedded ```
    fence by retrying with bare-fence lines stripped.
    """
    import yaml  # lazy — keep module import SDK/dep-free for the light CI job

    for raw in reversed(_YAML_BLOCK_RE.findall(transcript)):
        for candidate in (raw, _strip_bare_fences(raw)):
            try:
                data = yaml.safe_load(candidate)
            except yaml.YAMLError:
                continue
            if isinstance(data, dict) and "verdict" in data:
                return data
    return None


def has_feedback(transcript: str) -> bool:
    """True once the transcript carries the agent's feedback block — the
    predicate run_session uses to decide whether to prod for it (#683)."""
    return _first_feedback(transcript) is not None


def extract_rc_tag(transcript: str) -> str | None:
    """The throwaway ``vX.Y.Z-release-rc`` tag the agent cut, scanned out of the
    transcript INDEPENDENTLY of feedback parsing (release#709).

    Teardown of the rc tag must NOT be coupled to a successful feedback/verdict
    parse: when the subordinate session ends before emitting the feedback block
    (subscription throttle / turn-budget truncation), the rc may still have been
    cut earlier in the transcript, and leaving it dangling forces a manual
    ``gh release delete``. So this scans the WHOLE transcript for the strict
    verification-tag shape rather than reading ``feedback['rc']``.

    Returns the LAST such tag (the agent cuts the rc late, in step 4 / the
    feedback block — the last occurrence is the one it actually cut, not an
    earlier mention of the convention), or None if no verification tag appears.
    The strict :data:`_VERIFY_TAG_RE` shape means a stray mention of the literal
    word "release-rc" can't yield a destructive delete target.
    """
    matches = _VERIFY_TAG_RE.findall(transcript)
    if not matches:
        return None
    # findall with a capturing group returns the GROUP, not the whole match; use
    # finditer to recover the full matched tag, last-first.
    last = None
    for m in _VERIFY_TAG_RE.finditer(transcript):
        last = m.group(0)
    return last


def parse_feedback(transcript: str) -> dict:
    """The agent's ```yaml feedback mapping (a block with a ``verdict`` key).

    Raises rather than returning a partial — no feedback block is a failed run,
    and the message names the actual cause (the agent skipped the step) instead
    of a confusing YAML error on some unrelated block.
    """
    fb = _first_feedback(transcript)
    if fb is None:
        raise LiveFireError(
            "no ```yaml block parsed to a mapping with a 'verdict' key (missing, "
            "malformed YAML, or non-mapping) — the agent likely finished the work "
            "+ PR but skipped or botched the feedback step"
        )
    return fb


def feedback_or_fallback(transcript: str, *, rc_tag: str | None) -> dict:
    """The agent's parsed feedback, or — when the feedback/verdict block is
    missing — a structured FALLBACK mapping so the friction isn't silently lost
    (release#709).

    When the subordinate session ends before emitting the feedback block, the
    old behavior raised and lost the run entirely. Instead, synthesize a minimal
    feedback mapping carrying one ``feedback-skipped`` finding (with whatever rc
    tag was independently captured) so the run is still RECORDED in the #348
    inbox rather than a hard error. Teardown is handled separately off
    ``rc_tag`` — this only governs the harvest half.
    """
    fb = _first_feedback(transcript)
    if fb is not None:
        return fb
    return {
        "verdict": "feedback-skipped",
        "rc": rc_tag,
        "findings": [
            {
                "step": "feedback",
                "component": "livefire",
                "severity": "friction",
                "what": (
                    "subordinate agent ended before emitting the feedback/verdict "
                    "block (likely turn-budget / throttle truncation); findings lost"
                ),
                "expected": (
                    "the session should reach step 5 and emit the ```yaml feedback "
                    "block; re-harvest or re-run to recover this consumer's findings"
                ),
            }
        ],
    }


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


def _file_then_teardown(
    feedback: dict, *, consumer: str, rc_tag: str | None, dry_run: bool
) -> tuple[list[str], str | None]:
    """File findings, then ALWAYS attempt rc teardown. Teardown must run even
    when filing fails — otherwise a filing error (e.g. auth) would strand the
    throwaway ``-release-rc`` on the consumer.

    ``rc_tag`` is the tag captured INDEPENDENTLY of feedback parsing
    (:func:`extract_rc_tag`), NOT ``feedback['rc']`` — so teardown targets the
    rc the agent actually cut even when the feedback block was malformed or
    never emitted (release#709).

    Implemented as ``try/finally`` so that: a filing error propagates with its
    ORIGINAL traceback (no catch-and-re-raise); teardown still runs in the
    ``finally``; and if BOTH fail, the teardown exception raised in the
    ``finally`` supersedes the filing one — the stray release is the more urgent
    problem. Returns ``(filed, torn_down)`` on success.
    """
    rc = rc_tag if isinstance(rc_tag, str) else None
    filed: list[str] = []
    torn_down: str | None = None
    try:
        filed = file_findings(feedback, consumer=consumer, dry_run=dry_run)
    finally:
        torn_down = teardown_rc(consumer, rc, dry_run=dry_run)
    return filed, torn_down


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
            followup_prompt=FOLLOWUP_FEEDBACK_PROMPT,
            needs_followup=lambda t: not has_feedback(t),
        )
        transcript = "".join(sink)
        # Capture the rc tag straight from the transcript FIRST, decoupled from
        # feedback parsing — teardown must run off this even if the feedback
        # block is missing (release#709), so a throttle-truncated session can't
        # strand a dangling -release-rc on the consumer.
        rc_tag = extract_rc_tag(transcript)
        # Degrade a missing/malformed feedback block to a structured fallback
        # finding (so the friction is still recorded) rather than a hard error.
        feedback = feedback_or_fallback(transcript, rc_tag=rc_tag)

        filed, torn_down = _file_then_teardown(
            feedback, consumer=consumer, rc_tag=rc_tag, dry_run=dry_run
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


# ── Rollout (N consumers in parallel) ─────────────────────────────────────


DEFAULT_CONCURRENCY = 3
# A clean `owner/name` line — exactly one slash, no whitespace/extra columns. So
# a stray tab-separated or trailing-column line (if the verb's output ever grows
# one) is skipped rather than turned into a bogus clone target.
_OWNER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")


def registered_consumers() -> list[str]:
    """Every consumer in the fleet registry, via ``release-core admin repos
    list`` (the one source of truth — no re-parsing managed-repos.yaml here).

    Each line is stripped, then comments/blanks are skipped and only clean
    ``owner/name`` tokens are kept — defensive against any future trailing
    columns in the verb's output.
    """
    res = subprocess.run(["release-core", "admin", "repos", "list"], capture_output=True, text=True)
    if res.returncode != 0:
        raise LiveFireError(f"could not list registered consumers: {res.stderr.strip()}")
    consumers: list[str] = []
    for raw in res.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _OWNER_NAME_RE.fullmatch(line):
            consumers.append(line)
    return consumers


def summarize_rollout(results: list[dict]) -> dict:
    """Aggregate per-consumer summaries into a fleet rollout report — counts by
    verdict, total findings filed, and the list of consumers that errored."""
    by_verdict: dict[str, int] = {}
    findings_filed = 0
    errored: list[str] = []
    for r in results:
        verdict = str(r.get("verdict") or "unknown")
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
        findings_filed += len(r.get("findings_filed") or [])
        if r.get("error"):
            errored.append(r.get("consumer") or "?")
    return {
        "consumers": len(results),
        "by_verdict": by_verdict,
        "findings_filed": findings_filed,
        "errored": errored,
        "results": results,
    }


async def livefire_many(
    consumers: list[str],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    dry_run: bool = False,
    verbose: bool = False,
    keep_clone: bool = False,
) -> dict:
    """Run :func:`livefire_one` across N consumers concurrently (capped at
    ``concurrency``) and return :func:`summarize_rollout` over the results.

    A single consumer's failure is captured as an ``{error}`` entry, NOT raised
    — one broken consumer must not abort the rest of the fleet round (it shows
    up in the report's ``errored`` list). Each ``livefire_one`` clones into its
    own temp dir, so concurrent runs don't collide.
    """
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(consumer: str) -> dict:
        async with sem:
            try:
                return await livefire_one(
                    consumer, dry_run=dry_run, verbose=verbose, keep_clone=keep_clone
                )
            except Exception as e:  # noqa: BLE001 — one consumer must never abort the round
                # Capture ANY per-consumer failure (LiveFireError, or an
                # unexpected SDK/runtime error from run_session) so the rollout
                # continues. CancelledError is a BaseException and still
                # propagates, so cancellation is unaffected.
                return {
                    "consumer": consumer,
                    "verdict": "error",
                    "error": f"{type(e).__name__}: {e}",
                }

    results = await asyncio.gather(*(_one(c) for c in consumers))
    return summarize_rollout(list(results))
