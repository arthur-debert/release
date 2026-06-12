"""apply-ruleset — apply the canonical main-branch ruleset to a GitHub repo.

Usage:
  apply-ruleset [--dry-run] [--checks check1,check2,...]

Auto-detects:
  - repo from current git/gh context (gh repo view)
  - required checks: actual job names from the most recent default-branch
    run of each PR-trigger workflow (excluding copilot-review.yml — it
    isn't a gate — and `paths:`/`paths-ignore:`-filtered workflows, whose
    jobs are conditional and would deadlock unrelated PRs; see release#416).
    This catches matrix expansion (e.g. "Build (aarch64-apple-darwin)") and
    `name:` overrides that static parsing can't see. When no runs exist yet
    (the onboarding case), checks are derived statically from the workflow
    files themselves: a job that CALLS a reusable workflow
    (`uses: owner/repo/.github/workflows/x.yml@ref`) is resolved to its
    nested `<caller-job> / <called-job>` contexts by fetching the called
    workflow's definition at the pinned ref via the GitHub contents API —
    the bare caller-job name is never a reported context and would deadlock
    every PR (release#602).
Override checks with --checks (comma-separated). Use --dry-run to print the
payload without sending it.

Shell→Python migration: the ruleset payload
that was built with `yq -o json | jq` is now constructed as a Python dict and
PUT/POSTed via gh.rest(..., method=, body=) (the `gh api --input -` seam). The
payload is byte-identical to the old jq output, and the three stdout lines
(repo / ruleset / checks) plus the dry-run payload dump and exit codes are
preserved byte-for-byte.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys

from .. import cli, gh, yamlio

USAGE = __doc__ or ""

# Workflow basenames that are not PR gates and must be excluded.
_NON_GATE_WORKFLOWS = ("copilot-review.yml", "copilot-review.yaml")


def _usage_block() -> str:
    """The bash `--help` block: lines 2..first-blank with the leading `# ` gone.

    The bash printed `sed -n '2,/^$/p' "$0" | sed -E 's/^# ?//'`, i.e. the full
    leading comment block. We reproduce it from the docstring, stopping at the
    Shell→Python migration note (which has no bash counterpart).
    """
    lines = USAGE.strip("\n").splitlines()
    out: list[str] = []
    for line in lines:
        if line.startswith("Shell→Python migration"):
            break
        out.append(line)
    return "\n".join(out).rstrip("\n")


# --------------------------------------------------------------------------
# Pure helpers — fixture-tested, no network.
# --------------------------------------------------------------------------


def workflow_triggers(workflow: object) -> list[str]:
    """The `on:` triggers of a parsed workflow doc, normalized to a flat list.

    Mirrors the bash jq: a string `on:` → `[on]`; an array → itself; an object
    → its keys; anything else → `[]`.
    """
    if not isinstance(workflow, dict):
        return []
    on = workflow.get("on")
    if isinstance(on, str):
        return [on]
    if isinstance(on, list):
        return [str(x) for x in on]
    if isinstance(on, dict):
        return list(on.keys())
    return []


def is_pr_workflow(workflow: object) -> bool:
    """True if the workflow is triggered on `pull_request`."""
    return "pull_request" in workflow_triggers(workflow)


def pr_trigger_is_path_filtered(workflow: object) -> bool:
    """True if the workflow's `pull_request` trigger carries a `paths:` /
    `paths-ignore:` filter.

    A path-filtered workflow only runs when the PR touches matching files, so
    its job names are CONDITIONAL — they must never be auto-marked as globally
    required. A required check that never runs on an unrelated PR leaves the PR
    permanently BLOCKED (the check stays "expected", never "success"). The
    fleet of `bats` suites (changelog/audit/release-sync/…), each filtered to
    its own `tests/<area>/` paths, is the canonical case: auto-detection saw
    `bats` in the latest default-branch run and required it, deadlocking every
    workflow-only or docs-only PR (release#416).

    Only `pull_request` triggers WITHOUT a path filter are always-run gates and
    safe to require. A string/array `on:` (no per-trigger config) cannot carry
    a path filter, so it is treated as unfiltered.
    """
    if not isinstance(workflow, dict):
        return False
    on = workflow.get("on")
    if not isinstance(on, dict):
        return False
    pr = on.get("pull_request")
    if not isinstance(pr, dict):
        return False
    return bool(pr.get("paths") or pr.get("paths-ignore"))


def checks_json(checks: list[str]) -> list[dict]:
    """Map a list of check names → the `required_status_checks` array.

    Replaces `jq -R 'select(. != "") | {context: .}' | jq -s .`: drop empties,
    wrap each as `{"context": name}`, preserving order.
    """
    return [{"context": name} for name in checks if name != ""]


def build_payload(template: dict, checks: list[str]) -> dict:
    """Inject `checks` into the `required_status_checks` rule of `template`.

    Byte-for-byte equivalent of the bash jq:
        .rules |= map(if .type == "required_status_checks"
                      then .parameters.required_status_checks = $c else . end)
    `template` is the parsed main-protection.json.tmpl; this mutates a copy so
    the rest of the rules/conditions/bypass_actors are carried through verbatim.
    """
    import copy

    body = copy.deepcopy(template)
    contexts = checks_json(checks)
    for rule in body.get("rules", []):
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            rule.setdefault("parameters", {})["required_status_checks"] = contexts
    return body


def _existing_ruleset_id(rulesets: object, name: str) -> int | None:
    """First ruleset id whose name matches, or None. Mirrors the bash head -1."""
    for rs in rulesets or []:
        if isinstance(rs, dict) and rs.get("name") == name:
            return rs.get("id")
    return None


# --------------------------------------------------------------------------
# gh / fs boundary — check discovery.
# --------------------------------------------------------------------------


def _pr_workflow_paths(workflows_dir: str) -> list[str]:
    """Relative .github/workflows/<name> paths of workflows that can contribute
    globally-required checks.

    Globs *.yml then *.yaml (matching the bash loop order), parses each via yq,
    and keeps a workflow only when it is an always-run PR gate: triggered on
    pull_request, not a `_NON_GATE_WORKFLOWS` basename (copilot-review), and
    WITHOUT a `paths:` / `paths-ignore:` filter. Path-filtered workflows are
    conditional and must not be required — see `pr_trigger_is_path_filtered`
    (release#416).
    """
    import glob

    paths: list[str] = []
    names: list[str] = []
    for ext in ("*.yml", "*.yaml"):
        names.extend(sorted(glob.glob(os.path.join(workflows_dir, ext))))
    for path in names:
        base = os.path.basename(path)
        if base in _NON_GATE_WORKFLOWS:
            continue
        try:
            doc = yamlio.load(path)
        except yamlio.YamlError:
            continue
        if is_pr_workflow(doc) and not pr_trigger_is_path_filtered(doc):
            paths.append(f".github/workflows/{base}")
    return paths


def _checks_from_runs(repo: str, default_branch: str, paths: list[str]) -> list[str]:
    """Actual job-run names from the latest default-branch run of each workflow.

    Sorted-unique (the bash `sort -u`). Mirrors the three gh hops: workflow id by
    path → latest run id on the default branch → that run's job names.
    """
    found: set[str] = set()
    try:
        workflows_obj = gh.rest(f"repos/{repo}/actions/workflows")
    except gh.GhError:
        workflows_obj = None
    by_path = {}
    if isinstance(workflows_obj, dict):
        for wf in workflows_obj.get("workflows") or []:
            if isinstance(wf, dict) and wf.get("path"):
                by_path[wf["path"]] = wf.get("id")
    for path in paths:
        wid = by_path.get(path)
        if not wid:
            continue
        try:
            runs_obj = gh.rest(
                f"repos/{repo}/actions/workflows/{wid}/runs?branch={default_branch}&per_page=1"
            )
        except gh.GhError:
            continue
        runs = runs_obj.get("workflow_runs") if isinstance(runs_obj, dict) else None
        if not runs:
            continue
        run_id = runs[0].get("id") if isinstance(runs[0], dict) else None
        if not run_id:
            continue
        try:
            jobs_obj = gh.rest(f"repos/{repo}/actions/runs/{run_id}/jobs", paginate=True)
        except gh.GhError:
            continue
        for job in jobs_obj or []:
            if isinstance(job, dict) and job.get("name"):
                found.add(job["name"])
    return sorted(found)


# Matches the cross-repo reusable-workflow reference form:
#   owner/repo/.github/workflows/x.yml@ref
_USES_RE = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<path>[^@]+\.ya?ml)@(?P<ref>.+)$")

# GitHub caps reusable-workflow nesting at 4 levels; the same cap bounds the
# recursion (and breaks any accidental self-reference cycle).
_MAX_NESTING = 4


def job_display_name(job_id: str, job: object) -> str:
    """The name a job reports its check under: a static `name:` override when
    present, else the job ID. A `name:` carrying a `${{ … }}` expression can't
    be resolved statically, so the job ID is used (the runs-based detection is
    the one that sees rendered names)."""
    if isinstance(job, dict):
        name = job.get("name")
        if isinstance(name, str) and "${{" not in name:
            return name
    return job_id


def _called_job_included(job: object, with_values: dict) -> bool:
    """Whether a called workflow's job contributes a required context.

    A job-level `if:` of the exact form `inputs.<key>` (optionally
    `${{ … }}`-wrapped) is resolved against the caller's `with:` block — the
    fleet's reusable CI workflows gate their optional jobs precisely this way
    (`if: inputs.bats` / `if: inputs.e2e`), so a consumer that doesn't enable
    the feature must not get the context required. Any other `if:` expression
    is included: a job-level skip still reports a check run (conclusion
    `skipped`), which satisfies the ruleset — unlike a path-filtered workflow
    that never reports at all (release#416)."""
    if not isinstance(job, dict):
        return True
    cond = job.get("if")
    if cond is None:
        return True
    if not isinstance(cond, str):
        return bool(cond)
    expr = cond.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()
    match = re.fullmatch(r"inputs\.([A-Za-z_][A-Za-z0-9_-]*)", expr)
    if match is None:
        return True
    value = with_values.get(match.group(1))
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _fetch_called_workflow(uses: str, toplevel: str) -> object:
    """The parsed definition of a `uses:` target.

    ONE read mechanism per reference form, no fallbacks: a repo-local
    reference (`./.github/workflows/x.yml`) is read from the same working
    tree the caller workflow was read from; a cross-repo reference
    (`owner/repo/path@ref`) is fetched at its PINNED ref via the GitHub
    contents API — the only source that matches what the consumer actually
    runs (a local release checkout may be ahead of the consumer's `@vN`).
    Raises GhError/YamlError/ValueError on an unresolvable reference; the
    caller warns and contributes nothing for that job."""
    if uses.startswith("./"):
        return yamlio.load(os.path.join(toplevel, uses[2:]))
    match = _USES_RE.match(uses)
    if match is None:
        raise ValueError(f"unrecognized reusable-workflow reference: {uses!r}")
    obj = gh.rest("repos/{owner}/{repo}/contents/{path}?ref={ref}".format(**match.groupdict()))
    if not isinstance(obj, dict) or "content" not in obj:
        raise ValueError(f"no content for reusable workflow: {uses!r}")
    text = base64.b64decode(obj["content"]).decode("utf-8")
    return yamlio.loads(text)


def _job_contexts(
    job_id: str, job: object, *, toplevel: str, cache: dict[str, object], depth: int = 0
) -> list[str]:
    """The status-check contexts one workflow job reports.

    A plain job reports its own display name. A job that CALLS a reusable
    workflow (`uses:`) reports one context per called job, named
    `<caller-job> / <called-job>` — the bare caller name is never reported
    (release#602). Recurses through nested reusable calls (`a / b / c`),
    bounded by GitHub's own nesting cap."""
    uses = job.get("uses") if isinstance(job, dict) else None
    display = job_display_name(job_id, job)
    if not isinstance(uses, str):
        return [display]
    if depth >= _MAX_NESTING:
        print(f"warning: reusable-workflow nesting too deep at job '{job_id}'", file=sys.stderr)
        return []
    if uses not in cache:
        try:
            cache[uses] = _fetch_called_workflow(uses, toplevel)
        except (gh.GhError, yamlio.YamlError, ValueError) as exc:
            print(
                f"warning: cannot resolve reusable workflow '{uses}' "
                f"called by job '{job_id}': {exc}",
                file=sys.stderr,
            )
            cache[uses] = None
    doc = cache[uses]
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
        return []
    with_values = job.get("with") if isinstance(job.get("with"), dict) else {}
    out: list[str] = []
    for called_id, called in doc["jobs"].items():
        if not _called_job_included(called, with_values):
            continue
        for ctx in _job_contexts(
            called_id, called, toplevel=toplevel, cache=cache, depth=depth + 1
        ):
            out.append(f"{display} / {ctx}")
    return out


def _checks_from_workflows(toplevel: str, paths: list[str]) -> list[str]:
    """Static detection: the contexts the workflows report, sorted-unique.

    Used when no default-branch runs exist yet (the onboarding case). Jobs
    calling reusable workflows are resolved to their nested contexts via
    `_job_contexts`; plain jobs report their display name."""
    found: set[str] = set()
    cache: dict[str, object] = {}
    for path in paths:
        try:
            doc = yamlio.load(os.path.join(toplevel, path))
        except yamlio.YamlError:
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
            continue
        for job_id, job in doc["jobs"].items():
            found.update(_job_contexts(job_id, job, toplevel=toplevel, cache=cache))
    return sorted(found)


def _current_repo() -> str:
    """`gh repo view --json nameWithOwner -q .nameWithOwner`."""
    return gh.repo_view(json_fields=["nameWithOwner"], q=".nameWithOwner")


def _load_template(release_root: str) -> dict:
    """Parse rulesets/main-protection.json.tmpl into a dict, or raise FileNotFoundError."""
    tmpl = os.path.join(release_root, "rulesets", "main-protection.json.tmpl")
    if not os.path.isfile(tmpl):
        raise FileNotFoundError(tmpl)
    with open(tmpl, encoding="utf-8") as fh:
        return json.load(fh)


def _release_root() -> str:
    """The release/ checkout root, where rulesets/main-protection.json.tmpl lives.

    The verb lives at templates/commons/lib/release_core/release_core/verbs/, so
    the repo root is six parents up. (The bash resolved it from the shim's dir,
    bin/, but the template lives at <root>/rulesets either way.)
    """
    here = os.path.dirname(os.path.realpath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "..", "..", "..", ".."))


def main(argv: list[str]) -> int:
    try:
        values, _ = cli.parse(
            argv,
            [
                cli.Opt("--dry-run"),
                cli.Opt("--checks", takes_value=True, default=""),
            ],
            doc=_usage_block(),
        )
    except SystemExit as exc:
        return int(exc.code or 0)

    dry_run = bool(values["dry-run"])
    checks_override = values["checks"] or ""

    repo = _current_repo()

    if checks_override:
        # Override preserves the comma order (bash `tr ',' '\n'`); no sort.
        checks = checks_override.split(",")
    else:
        toplevel = gh.repo_root()
        workflows_dir = os.path.join(toplevel, ".github", "workflows")
        if not os.path.isdir(workflows_dir):
            print("no .github/workflows dir; pass --checks <names>", file=sys.stderr)
            return 1
        paths = _pr_workflow_paths(workflows_dir)
        default_branch = gh.rest(f"repos/{repo}")["default_branch"]
        checks = _checks_from_runs(repo, default_branch, paths)
        if not checks:
            checks = _checks_from_workflows(toplevel, paths)

    # `checks_str` in bash was a newline list; an empty override or no-runs/no-yq
    # leaves it empty → the same error + exit 1.
    checks = [c for c in checks if c != ""]
    if not checks:
        print("no required checks could be determined; pass --checks <names>", file=sys.stderr)
        return 1

    try:
        template = _load_template(_release_root())
    except FileNotFoundError as exc:
        print(f"template not found: {exc}", file=sys.stderr)
        return 1

    body = build_payload(template, checks)
    name = body["name"]

    try:
        rulesets = gh.rest(f"repos/{repo}/rulesets")
    except gh.GhError:
        rulesets = None
    existing_id = _existing_ruleset_id(rulesets, name)

    print(f"repo:    {repo}")
    print(f"ruleset: {name} (existing id: {existing_id if existing_id is not None else 'none'})")
    print(f"checks:  {','.join(checks)}")

    if dry_run:
        print()
        print("--- payload (dry-run, not sent) ---")
        print(json.dumps(body, indent=2))
        return 0

    if existing_id is not None:
        gh.rest(f"repos/{repo}/rulesets/{existing_id}", method="PUT", body=body)
        print("updated")
    else:
        gh.rest(f"repos/{repo}/rulesets", method="POST", body=body)
        print("created")
    return 0
