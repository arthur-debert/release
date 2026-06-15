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


def findings_to_issues(feedback: dict, *, consumer: str) -> list[dict]:
    """Map non-``ok`` findings to inbox issue specs.

    ``severity: ok`` entries are dropped (signal, not noise — per the prompt's
    routing note). Each spec is ``{component, title, body}`` ready for
    ``release-core issue file <component> <message>``.
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
        title = f"[{component}] live-fire ({severity}): {what[:80] or step}"
        body = (
            f"Surfaced by the live-fire harness (release#663) on `{consumer}`.\n\n"
            f"- **step:** {step}\n"
            f"- **severity:** {severity}\n"
            f"- **what:** {what or '(none)'}\n"
            f"- **expected:** {expected or '(none)'}\n"
        )
        specs.append({"component": component, "title": title, "body": body, "message": title})
    return specs


def teardown_command(consumer: str, rc_tag: str | None) -> list[str] | None:
    """The gh command that deletes the throwaway rc release + tag, or None when
    there is nothing to tear down (blocked run / no rc cut)."""
    if not rc_tag:
        return None
    tag = rc_tag.strip()
    if not tag or tag.lower().startswith("none") or "release-rc" not in tag:
        return None
    return ["gh", "release", "delete", tag, "--repo", consumer, "--yes", "--cleanup-tag"]


# ── I/O steps ─────────────────────────────────────────────────────────────


def file_findings(feedback: dict, *, consumer: str, dry_run: bool = False) -> list[str]:
    """File each non-ok finding into the release#348 inbox via
    ``release-core issue file``. Returns the messages filed (or that would be).
    """
    specs = findings_to_issues(feedback, consumer=consumer)
    filed: list[str] = []
    for spec in specs:
        cmd = ["release-core", "issue", "file", spec["component"], spec["body"]]
        if dry_run:
            print(f"[dry-run] would file: [{spec['component']}] {spec['title']}", file=sys.stderr)
        else:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                print(
                    f"[warn] failed to file finding [{spec['component']}]: {res.stderr.strip()}",
                    file=sys.stderr,
                )
                continue
        filed.append(spec["title"])
    return filed


def teardown_rc(consumer: str, rc_tag: str | None, *, dry_run: bool = False) -> str | None:
    """Delete the throwaway rc tag + GH pre-release on the consumer. Returns the
    tag torn down, or None if there was nothing to do."""
    cmd = teardown_command(consumer, rc_tag)
    if cmd is None:
        return None
    if dry_run:
        print(f"[dry-run] would teardown: {' '.join(cmd)}", file=sys.stderr)
        return rc_tag
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[warn] rc teardown failed for {rc_tag}: {res.stderr.strip()}", file=sys.stderr)
        return None
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
) -> dict:
    """Run the full single-consumer live-fire loop and return a summary dict.

    `consumer` is ``owner/name``. `dry_run` skips the *side-effecting* filing
    and teardown (the agent run still happens — that IS the test). Cloning into
    a fresh temp dir bounds the blast radius; the coverage PR it opens on the
    consumer's origin is real (the value left behind), the rc is torn down.
    """
    parent = Path(clone_parent) if clone_parent else Path(tempfile.mkdtemp(prefix="livefire-"))
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
    torn_down = teardown_rc(consumer, rc_tag if isinstance(rc_tag, str) else None, dry_run=dry_run)

    return {
        "consumer": consumer,
        "verdict": feedback.get("verdict"),
        "pr": feedback.get("pr"),
        "rc": rc_tag,
        "findings_filed": filed,
        "rc_torn_down": torn_down,
        "clone": str(clone),
    }
