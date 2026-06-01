"""release_lex verb — pure decision logic + arg parsing + sequencing helpers.

Post `scripts/release` retirement: release-lex drives each repo via the managed
`bin/diff-since-release` (should-release decision + commit count) and `bin/release`
(= release-cut, which reads the version from the manifest, computes the bump, and
dispatches release.yml; CI does the bump/CHANGELOG/commit/tag/build). The live
multi-repo orchestration (fetch/checkout/pull/submodule, release-cut dispatch, gh
run list/watch) is genuine side-effecting glue requiring real repos + GitHub, so
it is NOT unit-tested (that is the script's whole point). NO real release is ever
cut here. Tested: the github-slug map, the should-release decision over
diff-since-release output, the commit counter, the bump-kind / version
recognizers, the --only filter, the run-id extractor, the status-line renderer,
and the arg-parse + validation exit codes (data layer mocked via tmp dirs)."""

from __future__ import annotations

import os
import stat

import pytest
from release_core.verbs import release_lex as rlx

# --------------------------------------------------------------------------
# github_slug_for
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,slug",
    [
        ("comms", "lex-fmt/comms"),
        ("lex", "lex-fmt/lex"),
        ("tree-sitter", "lex-fmt/tree-sitter-lex"),
        ("vscode", "lex-fmt/vscode"),
        ("nvim", "lex-fmt/nvim"),
        ("lexed", "lex-fmt/lexed"),
    ],
)
def test_github_slug_known(key, slug):
    assert rlx.github_slug_for(key) == slug


def test_github_slug_unknown_is_empty():
    assert rlx.github_slug_for("nope") == ""


def test_repo_name_strips_owner():
    assert rlx._repo_name("tree-sitter") == "tree-sitter-lex"
    assert rlx._repo_name("comms") == "comms"


# --------------------------------------------------------------------------
# has_releasable_commits / count_commits  (the should-release decision over
# `diff-since-release` output)
# --------------------------------------------------------------------------

_DIFF_WITH_COMMITS = "Changes since v1.2.3:\n---\nabc1234 feat: a thing\ndef5678 fix: another\n"
_DIFF_NO_COMMITS = "Changes since v1.2.3:\n---\n"


def test_releasable_true_when_commits_present():
    assert rlx.has_releasable_commits(_DIFF_WITH_COMMITS, 0) is True


def test_releasable_false_when_no_commits():
    assert rlx.has_releasable_commits(_DIFF_NO_COMMITS, 0) is False


def test_releasable_false_when_rc_nonzero():
    # diff-since-release exits 1 when no release tags exist yet — nothing to
    # release (a first release is a human-driven explicit X.Y.Z).
    assert rlx.has_releasable_commits("No release tags found (on this branch)", 1) is False


def test_releasable_false_when_no_separator():
    # Unexpected shape (no `---`): be conservative, treat as nothing to release.
    assert rlx.has_releasable_commits("garbage with no separator", 0) is False


def test_releasable_ignores_blank_trailing_lines():
    assert rlx.has_releasable_commits("Changes since v1.0.0:\n---\n\n\n", 0) is False


def test_count_commits_counts_log_lines():
    assert rlx.count_commits(_DIFF_WITH_COMMITS) == 2


def test_count_commits_zero_when_empty():
    assert rlx.count_commits(_DIFF_NO_COMMITS) == 0


def test_count_commits_zero_when_no_separator():
    assert rlx.count_commits("no separator here") == 0


# --------------------------------------------------------------------------
# _looks_like_version (the loose `*.*.*` validation arm)
# --------------------------------------------------------------------------


def test_looks_like_version_true():
    assert rlx._looks_like_version("1.2.3")
    assert rlx._looks_like_version("a.b.c")
    assert rlx._looks_like_version("1.2.3-rc.1")


def test_looks_like_version_false():
    assert not rlx._looks_like_version("patchy")
    assert not rlx._looks_like_version("1.2")


# --------------------------------------------------------------------------
# parse_only / _is_allowed
# --------------------------------------------------------------------------


def test_parse_only_splits():
    assert rlx.parse_only("comms,lex") == ["comms", "lex"]


def test_parse_only_empty():
    assert rlx.parse_only("") == []


def test_is_allowed_no_filter():
    assert rlx._is_allowed("comms", [], "")


def test_is_allowed_in_list():
    assert rlx._is_allowed("lex", ["comms", "lex"], "comms,lex")


def test_is_allowed_not_in_list():
    assert not rlx._is_allowed("vscode", ["comms", "lex"], "comms,lex")


# --------------------------------------------------------------------------
# _first_database_id  ( `.[0].databaseId // empty` )
# --------------------------------------------------------------------------


def test_first_database_id_present():
    assert rlx._first_database_id('[{"databaseId": 12345}]') == "12345"


def test_first_database_id_empty_array():
    assert rlx._first_database_id("[]") == ""


def test_first_database_id_empty_string():
    assert rlx._first_database_id("") == ""


def test_first_database_id_null():
    assert rlx._first_database_id('[{"databaseId": null}]') == ""


def test_first_database_id_unparseable():
    assert rlx._first_database_id("not json") == ""


# --------------------------------------------------------------------------
# render_status_line
# --------------------------------------------------------------------------


def test_status_line_release_would_happen():
    line = rlx.render_status_line("comms", True, 0, 3)
    assert line == "comms              ⚠ would release: 3 commit(s) since last release"


def test_status_line_up_to_date():
    line = rlx.render_status_line("lex", False, 0, 0)
    assert line == "lex                ✓ up to date (no commits since last release)"


def test_status_line_no_tags():
    line = rlx.render_status_line("vscode", False, 1, 0)
    assert line == "vscode             ✗ no release tags yet (diff-since-release exited 1)"


# --------------------------------------------------------------------------
# _parse_args  (data-layer parse, no side effects)
# --------------------------------------------------------------------------


def test_parse_no_args_exits_64(capsys):
    rc = rlx.main([])
    assert rc == 64
    assert "Usage:" in capsys.readouterr().out


def test_parse_help_exits_0(capsys):
    rc = rlx.main(["--help"])
    assert rc == 0
    assert "Usage:" in capsys.readouterr().out


def test_parse_unknown_arg_exits_64(capsys):
    rc = rlx.main(["patch", "--bogus"])
    assert rc == 64
    assert "unknown arg: --bogus" in capsys.readouterr().err


def test_parse_bad_bump_kind_exits_64(capsys, tmp_path):
    _make_repo(tmp_path, with_tools=True)
    rc = rlx.main(["frobnicate", "--comms", str(tmp_path)])
    assert rc == 64
    assert "bad bump-kind" in capsys.readouterr().err


def test_parse_no_repos_exits_64(capsys):
    rc = rlx.main(["patch"])
    assert rc == 64
    assert "no repo paths supplied" in capsys.readouterr().err


def test_status_mode_skips_bump_validation_but_needs_repos(capsys):
    rc = rlx.main(["--status"])
    assert rc == 64
    assert "no repo paths supplied" in capsys.readouterr().err


# --------------------------------------------------------------------------
# _validate  (path + managed-tool existence -> exit 1)
# --------------------------------------------------------------------------


def _make_repo(path, *, with_tools: bool, missing=()):
    """Materialize a repo dir with executable bin/<tool> managed release tools,
    optionally omitting some to exercise the missing-tool abort."""
    binv = os.path.join(str(path), "bin")
    os.makedirs(binv, exist_ok=True)
    if with_tools:
        for tool in ("diff-since-release", "release"):
            if tool in missing:
                continue
            p = os.path.join(binv, tool)
            with open(p, "w") as fh:
                fh.write("#!/usr/bin/env bash\n")
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_validate_not_a_directory_exits_1(capsys, tmp_path):
    missing = tmp_path / "nope"
    rc = rlx.main(["patch", "--comms", str(missing)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not a directory" in err
    assert "(for --comms)" in err


def test_validate_missing_release_tool_exits_1(capsys, tmp_path):
    _make_repo(tmp_path, with_tools=True, missing=("release",))
    rc = rlx.main(["patch", "--comms", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "missing bin/release" in err
    assert "release-sync" in err


def test_validate_missing_diff_tool_exits_1(capsys, tmp_path):
    _make_repo(tmp_path, with_tools=True, missing=("diff-since-release",))
    rc = rlx.main(["patch", "--comms", str(tmp_path)])
    assert rc == 1
    assert "missing bin/diff-since-release" in capsys.readouterr().err


def test_validate_non_executable_tool_exits_1(capsys, tmp_path):
    _make_repo(tmp_path, with_tools=True)
    # Strip the exec bit from one tool: the executability check must fail.
    p = os.path.join(str(tmp_path), "bin", "release")
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    rc = rlx.main(["patch", "--comms", str(tmp_path)])
    assert rc == 1
    assert "missing bin/release" in capsys.readouterr().err


def test_validate_status_mode_only_needs_diff_tool(tmp_path):
    # In --status mode, a repo with only bin/diff-since-release (no bin/release)
    # passes validation (returns None — proceed).
    _make_repo(tmp_path, with_tools=True, missing=("release",))
    cfg = {
        "status_mode": True,
        "bump_kind": "status",
        "dry_run": False,
        "only": "",
        "repos": {"comms": str(tmp_path)},
    }
    assert rlx._validate(cfg) is None
