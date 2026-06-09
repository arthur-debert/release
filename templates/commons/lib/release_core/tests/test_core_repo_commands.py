"""``release_core.repo_commands`` — the component detector behind how-to + the
runnable task verbs (release#507).

A repo is a composition of components; the Kind says WHERE to look, the manifest
says WHAT the command is. These tests pin that the detector reads the REAL repo
(never a per-Kind hardcode), package-manager-aware, and never crashes on a
missing/garbled manifest.
"""

from __future__ import annotations

import json
import os

from release_core import repo_commands as rc


def _pkg(root, scripts: dict, *, pm: str = "npm"):
    (root / "package.json").write_text(json.dumps({"scripts": scripts}))
    lock = {"npm": "package-lock.json", "pnpm": "pnpm-lock.yaml", "yarn": "yarn.lock"}[pm]
    (root / lock).write_text("")


# --- package manager ------------------------------------------------------


def test_detect_pm_from_lockfile(tmp_path):
    assert rc.detect_pm(str(tmp_path)) == "npm"  # default, no lockfile
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert rc.detect_pm(str(tmp_path)) == "pnpm"


def test_detect_pm_yarn(tmp_path):
    (tmp_path / "yarn.lock").write_text("")
    assert rc.detect_pm(str(tmp_path)) == "yarn"


# --- unit: the real scripts, not a guessed `npm test` ---------------------


def test_unit_surfaces_real_test_unit_not_bare_npm_test(tmp_path):
    # lexed's shape: test:unit + test:e2e, NO bare `test`. Must NOT assert npm test.
    _pkg(tmp_path, {"test:unit": "vitest", "test:e2e": "playwright test"})
    unit = rc.unit_commands(str(tmp_path))
    assert len(unit) == 1
    assert unit[0].display == "npm run test:unit --run"  # vitest → --run injected
    assert "npm test" not in unit[0].display
    # e2e is a SEPARATE suite, never folded into unit.
    e2e = rc.e2e_commands(str(tmp_path))
    assert [c.display for c in e2e] == ["npm run test:e2e"]


def test_unit_pnpm_vitest_no_dashdash_leak(tmp_path):
    # pnpm/yarn forward args directly; a literal `--` leaks into vitest's argv
    # (`vitest -- --run` → --run becomes a positional → watch mode hangs). The
    # --run flag must reach vitest cleanly, without the separator.
    _pkg(tmp_path, {"test:unit": "vitest"}, pm="pnpm")
    unit = rc.unit_commands(str(tmp_path))
    assert unit[0].argv == ["pnpm", "run", "test:unit", "--run"]  # no `--`
    # npm, by contrast, DOES need the separator.
    (tmp_path / "pnpm-lock.yaml").unlink()
    (tmp_path / "package-lock.json").write_text("")
    npm = rc.unit_commands(str(tmp_path))
    assert npm[0].argv == ["npm", "run", "test:unit", "--", "--run"]


def test_unit_falls_back_to_bare_test_script(tmp_path):
    # phos-app's shape: a plain `test` (vitest run) + test:e2e.
    _pkg(tmp_path, {"test": "vitest run", "test:e2e": "playwright test"}, pm="pnpm")
    unit = rc.unit_commands(str(tmp_path))
    assert [c.display for c in unit] == ["pnpm run test"]  # already `vitest run` → no --run


def test_unit_no_node_test_script_is_empty(tmp_path):
    _pkg(tmp_path, {"build": "vite build"})
    assert rc.unit_commands(str(tmp_path)) == []


# --- rust component, including the src-tauri facet ------------------------


def test_unit_rust_root(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    unit = rc.unit_commands(str(tmp_path))
    assert [c.display for c in unit] == ["cargo test --all-features"]
    assert unit[0].cwd is None


def test_unit_tauri_fans_out_node_then_rust(tmp_path):
    # tauri = node + rust(src-tauri). Cheap-first: node before the rust compile.
    _pkg(tmp_path, {"test:unit": "vitest"}, pm="pnpm")
    (tmp_path / "src-tauri").mkdir()
    (tmp_path / "src-tauri" / "Cargo.toml").write_text("[package]\nname='app'\n")
    unit = rc.unit_commands(str(tmp_path))
    assert [c.label for c in unit] == ["node", "rust"]
    assert unit[1].cwd == "src-tauri"
    assert "cargo test" in unit[1].display


# --- make component -------------------------------------------------------


def test_unit_make_test_target(tmp_path):
    (tmp_path / "Makefile").write_text("test:\n\tbusted tests\n")
    unit = rc.unit_commands(str(tmp_path))
    assert [c.display for c in unit] == ["make test"]


def test_unit_nvim_app_bin_test_all_wins(tmp_path):
    # The canonical fleet nvim runner is app-bin/test-all (NOT `make test`).
    # It must win over a Makefile target when both exist.
    appbin = tmp_path / "app-bin"
    appbin.mkdir()
    runner = appbin / "test-all"
    runner.write_text("#!/usr/bin/env bash\n")
    runner.chmod(0o755)
    (tmp_path / "Makefile").write_text("test:\n\tbusted tests\n")
    unit = rc.unit_commands(str(tmp_path))
    assert [c.display for c in unit] == ["app-bin/test-all"]
    assert unit[0].label == "nvim"


def test_unit_app_bin_test_all_must_be_executable(tmp_path):
    # A non-executable app-bin/test-all is not a runnable entry — fall through.
    appbin = tmp_path / "app-bin"
    appbin.mkdir()
    (appbin / "test-all").write_text("#!/usr/bin/env bash\n")  # not chmod +x
    assert rc.unit_commands(str(tmp_path)) == []


def test_unit_nvim_busted_fallback_gated_to_layout(tmp_path):
    # An nvim layout (lua/ dir) + tests/ → busted tests (the wrapper's fallback).
    (tmp_path / "lua").mkdir()
    (tmp_path / "tests").mkdir()
    unit = rc.unit_commands(str(tmp_path))
    assert [c.display for c in unit] == ["busted tests"]
    assert unit[0].label == "nvim"


def test_unit_generic_tests_dir_is_not_busted(tmp_path):
    # A bare tests/ dir WITHOUT an nvim layout must NOT be guessed as busted —
    # that would misclassify any repo with a tests/ folder.
    (tmp_path / "tests").mkdir()
    assert rc.unit_commands(str(tmp_path)) == []


# --- CI check-command fallback (manifest-empty repos) ---------------------
#
# These tests monkeypatch rc.yamlio.load (the suite has no `yq`), mirroring the
# hermetic pattern in test_core_apply_ruleset.py — the workflow file's bytes are
# placeholders; the parsed dict is supplied directly.


def _wf_caller_doc(check_command, *, uses=None):
    """A parsed CI-caller workflow doc invoking a release reusable workflow with
    a `check-command:` input."""
    uses = uses or "arthur-debert/release/.github/workflows/nvim-plugin.yml@v2"
    return {
        "name": "CI",
        "on": "push",
        "jobs": {"test": {"uses": uses, "with": {"check-command": check_command}}},
    }


def _stub_workflows(tmp_path, monkeypatch, docs):
    """Create placeholder .github/workflows/<name> files and monkeypatch
    yamlio.load to return docs[basename]. A doc value may be an Exception type/
    instance to simulate a parse failure."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    for name in docs:
        (wf / name).write_text("placeholder")

    def fake_load(path):
        doc = docs[os.path.basename(path)]
        if isinstance(doc, BaseException):
            raise doc
        return doc

    monkeypatch.setattr(rc.yamlio, "load", fake_load)


def test_unit_falls_back_to_ci_check_command(tmp_path, monkeypatch):
    # lex-fmt/nvim's shape: NO manifest test, real suite wired via the canonical
    # caller's check-command:. The fallback must surface it (label "ci").
    _stub_workflows(
        tmp_path, monkeypatch, {"ci.yml": _wf_caller_doc("cd test && bash run_tests.sh")}
    )
    unit = rc.unit_commands(str(tmp_path))
    assert len(unit) == 1
    assert unit[0].display == "cd test && bash run_tests.sh"
    assert unit[0].argv == ["bash", "-c", "cd test && bash run_tests.sh"]
    assert unit[0].label == "ci"


def test_unit_check_command_is_fallback_only_not_when_manifest_present(tmp_path, monkeypatch):
    # A real manifest test (Cargo.toml) AND a check-command workflow: the manifest
    # wins; the check-command is NEVER added (no double-run).
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    _stub_workflows(
        tmp_path, monkeypatch, {"ci.yml": _wf_caller_doc("cd test && bash run_tests.sh")}
    )
    unit = rc.unit_commands(str(tmp_path))
    assert [c.display for c in unit] == ["cargo test --all-features"]
    assert all(c.label != "ci" for c in unit)


def test_unit_check_command_ignores_non_release_caller(tmp_path, monkeypatch):
    # A workflow that uses some OTHER reusable workflow must not be mined.
    doc = _wf_caller_doc("echo nope", uses="actions/some-other/.github/workflows/x.yml@v1")
    _stub_workflows(tmp_path, monkeypatch, {"ci.yml": doc})
    assert rc.unit_commands(str(tmp_path)) == []


def test_unit_check_command_garbled_workflow_no_crash(tmp_path, monkeypatch):
    # yamlio.load raising (yq parse failure) or a non-dict doc must not crash →
    # the fallback contributes nothing.
    from release_core import yamlio

    _stub_workflows(
        tmp_path,
        monkeypatch,
        {
            "broken.yml": yamlio.YamlError("yq parse failure"),
            "scalar.yml": "just-a-string",  # non-dict doc
        },
    )
    assert rc.unit_commands(str(tmp_path)) == []


def test_ci_check_command_missing_dir_returns_none(tmp_path):
    # No .github/workflows/ at all — never touches yamlio.
    assert rc._ci_check_command(str(tmp_path)) is None


# --- build: single app-root command ---------------------------------------


def test_build_tauri_is_single_pm_tauri_build(tmp_path):
    _pkg(tmp_path, {"build": "vite build"}, pm="pnpm")
    (tmp_path / "src-tauri").mkdir()
    (tmp_path / "src-tauri" / "Cargo.toml").write_text("[package]\nname='app'\n")
    build = rc.build_command(str(tmp_path))
    assert build.display == "pnpm tauri build"  # one orchestrated cmd, not a fan-out


def test_build_tauri_npm_uses_npx(tmp_path):
    _pkg(tmp_path, {"build": "vite build"}, pm="npm")
    (tmp_path / "src-tauri").mkdir()
    (tmp_path / "src-tauri" / "Cargo.toml").write_text("[package]\nname='app'\n")
    assert rc.build_command(str(tmp_path)).argv == ["npx", "--no-install", "tauri", "build"]


def test_build_node_uses_build_script(tmp_path):
    _pkg(tmp_path, {"build": "tsc && vite build"}, pm="npm")
    assert rc.build_command(str(tmp_path)).display == "npm run build"


def test_build_rust(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    assert rc.build_command(str(tmp_path)).display == "cargo build --release"


def test_build_none_when_nothing(tmp_path):
    assert rc.build_command(str(tmp_path)) is None


# --- run ------------------------------------------------------------------


def test_run_prefers_dev_script(tmp_path):
    _pkg(tmp_path, {"start": "electron .", "dev": "vite"}, pm="npm")
    assert rc.run_command(str(tmp_path)).display == "npm run dev"  # dev wins over start


def test_run_rust(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    assert rc.run_command(str(tmp_path)).argv == ["cargo", "run", "--"]


# --- docs: orthogonal, any Kind ------------------------------------------


def test_docs_surfaced_alongside_rust(tmp_path):
    # A rust-cli that ALSO carries mkdocs docs — the docs facet must surface.
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    (tmp_path / "mkdocs.yml").write_text("site_name: x\n")
    docs = rc.docs_commands(str(tmp_path))
    assert docs is not None
    assert docs.build.argv == ["mkdocs", "build", "--strict", "--config-file", "mkdocs.yml"]


def test_docs_none_when_absent(tmp_path):
    assert rc.docs_commands(str(tmp_path)) is None


# --- robustness: garbled / missing manifest never crashes -----------------


def test_garbled_package_json_is_graceful(tmp_path):
    (tmp_path / "package.json").write_text("{ not json")
    assert rc.unit_commands(str(tmp_path)) == []
    assert rc.build_command(str(tmp_path)) is None


def test_resolve_empty_repo(tmp_path):
    got = rc.resolve(str(tmp_path))
    assert got.unit == [] and got.e2e == [] and got.build is None and got.run is None
    assert got.docs is None and got.deps is None
