"""``release_core.repo_commands`` — the component detector behind how-to + the
runnable task verbs (release#507).

A repo is a composition of components; the Kind says WHERE to look, the manifest
says WHAT the command is. These tests pin that the detector reads the REAL repo
(never a per-Kind hardcode), package-manager-aware, and never crashes on a
missing/garbled manifest.
"""

from __future__ import annotations

import json

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
