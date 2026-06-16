"""canary_init verb — idempotent create/reset of a canary repo (#604).

Fully offline: the fixture-marker mapping, repo resolution with every
fail-closed refusal, the registry text-edit, the forbidden-secrets check, and
the main() pipeline with every gh/git seam monkeypatched (create-if-missing,
seed-skip idempotence, --reset force-push, secrets fail-closed). The live
create/seed/policy glue is validated by a real `canary init` run, not here.
"""

from __future__ import annotations

import os
import sys
import types

import pytest
from release_core.verbs import canary_init
from release_core.verbs.canary_run import CanaryError

# The release checkout this test file lives in (tests/ → release_core pkg →
# lib → commons → templates → root).
REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))

MANIFEST = """\
# fleet manifest (comment must survive edits)
projects:
  dodot:
    - { repo: arthur-debert/dodot, path: dodot }

# canary block comment
canaries:
  rust: arthur-debert/release-canary-rust
"""


# ── find_fixture (the family→fixture mapping) ────────────────────────────────


def _make_fixture(root, kind: str, family: str) -> str:
    d = root / "fixtures" / kind
    d.mkdir(parents=True)
    (d / ".canary-family").write_text(family + "\n")
    return str(d)


def test_find_fixture_by_marker(tmp_path):
    want = _make_fixture(tmp_path, "rust-cli", "rust")
    _make_fixture(tmp_path, "vscode-ext", "vscode-ext")
    assert canary_init.find_fixture(str(tmp_path / "fixtures"), "rust") == want


def test_find_fixture_unknown_family_fails(tmp_path):
    _make_fixture(tmp_path, "rust-cli", "rust")
    with pytest.raises(CanaryError, match="no fixture for family 'npm'"):
        canary_init.find_fixture(str(tmp_path / "fixtures"), "npm")


def test_find_fixture_duplicate_family_fails(tmp_path):
    _make_fixture(tmp_path, "rust-cli", "rust")
    _make_fixture(tmp_path, "rust-cli-2", "rust")
    with pytest.raises(CanaryError, match="more than one fixture"):
        canary_init.find_fixture(str(tmp_path / "fixtures"), "rust")


def test_find_fixture_missing_root_fails(tmp_path):
    with pytest.raises(CanaryError, match="no fixture"):
        canary_init.find_fixture(str(tmp_path / "nope"), "rust")


def test_real_tree_rust_fixture_is_wired():
    # Binds the shipped fixture to the verb: tests/fixtures/rust-cli/ claims
    # the `rust` family, so init seeds exactly what the live canary carries.
    found = canary_init.find_fixture(os.path.join(REPO_ROOT, "tests", "fixtures"), "rust")
    assert found.endswith(os.path.join("tests", "fixtures", "rust-cli"))
    # The seed never carries the round/repo-specific bits init generates.
    for absent in ("bin", ".claude", "CLAUDE.md"):
        assert not os.path.exists(os.path.join(found, absent))
    # And it is a real rust-cli: workspace + thin callers + e2e contract.
    for present in (
        "Cargo.toml",
        "Cargo.lock",
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        ".release-sync.yaml",
        "tests/e2e/canary.bats",
    ):
        assert os.path.exists(os.path.join(found, present)), present


def test_real_tree_vscode_ext_fixture_is_wired():
    # The second family (#605): tests/fixtures/vscode-ext/ claims the
    # `vscode-ext` family — proof that adding a family changed no verb code.
    found = canary_init.find_fixture(os.path.join(REPO_ROOT, "tests", "fixtures"), "vscode-ext")
    assert found.endswith(os.path.join("tests", "fixtures", "vscode-ext"))
    for absent in ("bin", ".claude", "CLAUDE.md", ".canary-secrets"):
        assert not os.path.exists(os.path.join(found, absent))
    # A real vscode-ext: extension sources + the lockfile vscode-ext.yml's
    # `npm ci` requires + thin callers + the fragment-model changelog dir.
    for present in (
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "src/extension.ts",
        "src/liveness.test.ts",
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        "CHANGELOG/.gitkeep",
    ):
        assert os.path.exists(os.path.join(found, present)), present
    # detect-kind classifies the fixture as vscode-ext (the `@vscode/vsce`
    # devDependency is the signal release-core init keys off).
    from release_core import manifest

    assert manifest.detect_kind(found) == "vscode-ext"
    # Fencing: the release caller disables every marketplace publish path.
    with open(os.path.join(found, ".github", "workflows", "release.yml"), encoding="utf-8") as fh:
        caller = fh.read()
    assert "publish-marketplace: false" in caller
    assert "publish-openvsx: false" in caller


def test_real_tree_rust_fixture_declares_the_signing_pair():
    # Cert-only signing (#587 OQ3): the rust family converges exactly the
    # cert pair — never the ASC trio, never the publish trio.
    found = canary_init.find_fixture(os.path.join(REPO_ROOT, "tests", "fixtures"), "rust")
    assert canary_init.required_secrets(found) == canary_init.SIGNING_SECRETS


# ── required_secrets (the .canary-secrets marker) ────────────────────────────


def test_required_secrets_without_marker_is_empty(tmp_path):
    assert canary_init.required_secrets(str(tmp_path)) == ()


def test_required_secrets_parses_names_skipping_comments(tmp_path):
    (tmp_path / ".canary-secrets").write_text(
        "# cert-only signing\n\nAPPLE_CERTIFICATE_P12_BASE64\nAPPLE_CERTIFICATE_PASSWORD\n"
    )
    assert canary_init.required_secrets(str(tmp_path)) == canary_init.SIGNING_SECRETS


@pytest.mark.parametrize("banned", ["NPM_TOKEN", "ASC_API_KEY_BASE64"])
def test_required_secrets_forbidden_name_refused(tmp_path, banned):
    (tmp_path / ".canary-secrets").write_text(f"{banned}\n")
    with pytest.raises(CanaryError, match="never publish and never notarize"):
        canary_init.required_secrets(str(tmp_path))


def test_required_secrets_unknown_name_refused(tmp_path):
    (tmp_path / ".canary-secrets").write_text("SOME_OTHER_SECRET\n")
    with pytest.raises(CanaryError, match="cannot source"):
        canary_init.required_secrets(str(tmp_path))


def test_copy_fixture_excludes_both_markers(tmp_path):
    src = tmp_path / "fixture"
    src.mkdir()
    (src / ".canary-family").write_text("rust\n")
    (src / ".canary-secrets").write_text("APPLE_CERTIFICATE_P12_BASE64\n")
    (src / "Cargo.toml").write_text("[workspace]\n")
    dest = tmp_path / "seed"
    dest.mkdir()
    canary_init._copy_fixture(str(src), str(dest))
    assert os.path.exists(dest / "Cargo.toml")
    assert not os.path.exists(dest / ".canary-family")
    assert not os.path.exists(dest / ".canary-secrets")


# ── resolve_repo (fail-closed refusals) ──────────────────────────────────────

REGISTRY = {"rust": "arthur-debert/release-canary-rust"}
PROJECTS = {"arthur-debert/dodot"}


def test_resolve_registered_family_uses_registry():
    repo = canary_init.resolve_repo("rust", REGISTRY, PROJECTS, reset=False)
    assert repo == "arthur-debert/release-canary-rust"


def test_resolve_unregistered_create_derives_conventional_name():
    repo = canary_init.resolve_repo("vscode-ext", REGISTRY, PROJECTS, reset=False)
    assert repo == "arthur-debert/release-canary-vscode-ext"


def test_reset_refused_off_registry():
    with pytest.raises(CanaryError, match="--reset refused.*not in the canaries: registry"):
        canary_init.resolve_repo("vscode-ext", REGISTRY, PROJECTS, reset=True)


def test_refuses_a_projects_fleet_consumer():
    registry = {"rust": "arthur-debert/dodot"}
    with pytest.raises(CanaryError, match="projects: fleet consumer"):
        canary_init.resolve_repo("rust", registry, PROJECTS, reset=False)


def test_refuses_a_non_canary_repo_name():
    registry = {"rust": "arthur-debert/padz"}
    with pytest.raises(CanaryError, match="not a release-canary-"):
        canary_init.resolve_repo("rust", registry, set(), reset=False)


# ── register_canary_text (registry edit) ─────────────────────────────────────


def test_register_appends_to_the_canaries_block():
    out = canary_init.register_canary_text(MANIFEST, "vscode-ext", "arthur-debert/rc-x")
    lines = out.splitlines()
    i = lines.index("  rust: arthur-debert/release-canary-rust")
    assert lines[i + 1] == "  vscode-ext: arthur-debert/rc-x"
    # Comments and the projects: block survive untouched.
    assert "# fleet manifest (comment must survive edits)" in out
    assert "# canary block comment" in out
    assert "arthur-debert/dodot" in out


def test_register_is_idempotent():
    assert (
        canary_init.register_canary_text(MANIFEST, "rust", "arthur-debert/release-canary-rust")
        == MANIFEST
    )


def test_register_without_a_canaries_block_fails():
    with pytest.raises(CanaryError, match="no `canaries:` block"):
        canary_init.register_canary_text(
            "projects:\n  d:\n    - { repo: a/d, path: d }\n", "r", "x"
        )


def test_register_against_the_real_manifest_is_idempotent_for_rust():
    with open(os.path.join(REPO_ROOT, "managed-repos.yaml"), encoding="utf-8") as fh:
        text = fh.read()
    assert (
        canary_init.register_canary_text(text, "rust", "arthur-debert/release-canary-rust") == text
    )
    grown = canary_init.register_canary_text(
        text, "vscode-ext", "arthur-debert/release-canary-vscode-ext"
    )
    assert "  vscode-ext: arthur-debert/release-canary-vscode-ext\n" in grown


# ── forbidden secrets ────────────────────────────────────────────────────────


def test_forbidden_secrets_detected():
    names = ["RELEASE_TOKEN", "CRATES_IO_KEY", "NPM_TOKEN"]
    assert canary_init.forbidden_secrets_present(names) == ["CRATES_IO_KEY", "NPM_TOKEN"]


def test_forbidden_secrets_empty_when_clean():
    assert canary_init.forbidden_secrets_present(["RELEASE_TOKEN"]) == []


# ── arg parsing ──────────────────────────────────────────────────────────────


def test_family_is_required(capsys):
    assert canary_init.main([]) == 64
    assert "--family is required" in capsys.readouterr().err


def test_unknown_arg_is_usage_error(capsys):
    assert canary_init.main(["--family", "rust", "--force"]) == 64
    assert "unknown arg" in capsys.readouterr().err


def test_help_exits_zero(capsys):
    assert canary_init.main(["--help"]) == 0
    assert "idempotent create/reset" in capsys.readouterr().out


# ── main() pipeline (gh/git seams monkeypatched) ─────────────────────────────


class Calls:
    """Records every seam main() drives."""

    def __init__(self):
        self.created: list[str] = []
        self.cloned: list[str] = []
        self.built = 0
        self.pushed: list[bool] = []  # the force flag per push
        self.token_installed: list[str] = []
        self.policy_applied = 0


@pytest.fixture
def seams(tmp_path, monkeypatch):
    """A fake release checkout + every gh/git seam stubbed to the happy path."""
    root = tmp_path / "release"
    root.mkdir()
    (root / "managed-repos.yaml").write_text(MANIFEST)
    fixtures = root / "tests" / "fixtures" / "rust-cli"
    fixtures.mkdir(parents=True)
    (fixtures / ".canary-family").write_text("rust\n")

    calls = Calls()
    monkeypatch.setattr(canary_init.gh, "repo_root", lambda start=None: str(root))
    monkeypatch.setattr(canary_init, "_gh_preflight", lambda: True)
    monkeypatch.setattr(canary_init, "_floating_major", lambda: "v2")
    monkeypatch.setattr(canary_init, "_repo_exists", lambda repo: True)
    monkeypatch.setattr(canary_init, "_repo_create", lambda repo, d: calls.created.append(repo))
    monkeypatch.setattr(
        canary_init, "_clone", lambda repo, dest: (calls.cloned.append(repo), os.makedirs(dest))
    )
    monkeypatch.setattr(canary_init, "_main_seeded", lambda dest: True)
    monkeypatch.setattr(
        canary_init,
        "_build_seed",
        lambda **kw: setattr(calls, "built", calls.built + 1),
    )
    monkeypatch.setattr(canary_init, "_push_seed", lambda dest, force: calls.pushed.append(force))
    monkeypatch.setattr(canary_init.gh, "secret_list", lambda repo: ["RELEASE_TOKEN"])
    monkeypatch.setattr(
        canary_init,
        "_install_token",
        lambda repo: (calls.token_installed.append(repo), 0)[1],
    )
    monkeypatch.setattr(
        canary_init,
        "_apply_policy",
        lambda dest: (setattr(calls, "policy_applied", calls.policy_applied + 1), 0)[1],
    )
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(canary_init, "_workdir_root", lambda: str(tmp_path / "work"))
    return calls, root


def test_idempotent_rerun_converges_without_touching_anything(seams, capsys):
    calls, root = seams
    assert canary_init.main(["--family", "rust"]) == 0
    out = capsys.readouterr().out
    assert calls.created == []  # exists → no create
    assert calls.built == 0 and calls.pushed == []  # seeded → no re-seed
    assert calls.token_installed == []  # token present + tty → kept
    assert calls.policy_applied == 1  # policy upsert always converges
    assert "skip create" in out and "skip (use --reset" in out
    assert "already registered" in out
    # The registry was not rewritten.
    assert (root / "managed-repos.yaml").read_text() == MANIFEST


def test_create_if_missing_seeds_and_pushes(seams, monkeypatch, capsys):
    calls, _root = seams
    monkeypatch.setattr(canary_init, "_repo_exists", lambda repo: False)
    monkeypatch.setattr(canary_init, "_main_seeded", lambda dest: False)
    assert canary_init.main(["--family", "rust"]) == 0
    assert calls.created == ["arthur-debert/release-canary-rust"]
    assert calls.built == 1
    assert calls.pushed == [False]  # a first seed is a plain push, never forced


def test_reset_force_pushes_the_seed(seams):
    calls, _root = seams
    assert canary_init.main(["--family", "rust", "--reset"]) == 0
    assert calls.built == 1
    assert calls.pushed == [True]


def test_reset_off_registry_refuses_before_any_remote_call(seams, capsys):
    calls, _root = seams
    assert canary_init.main(["--family", "vscode-ext", "--reset"]) == 2
    err = capsys.readouterr().err
    assert "--reset refused" in err
    assert calls.created == [] and calls.cloned == [] and calls.pushed == []


def test_unknown_family_without_fixture_fails_setup(seams, capsys):
    _calls, _root = seams
    assert canary_init.main(["--family", "npm"]) == 2
    assert "no fixture for family 'npm'" in capsys.readouterr().err


def test_forbidden_secret_fails_closed(seams, monkeypatch, capsys):
    calls, _root = seams
    monkeypatch.setattr(
        canary_init.gh, "secret_list", lambda repo: ["RELEASE_TOKEN", "CRATES_IO_KEY"]
    )
    assert canary_init.main(["--family", "rust"]) == 1
    err = capsys.readouterr().err
    assert "forbidden publish secret" in err and "CRATES_IO_KEY" in err
    assert calls.token_installed == []
    assert calls.policy_applied == 0  # fails before policy: the repo is mis-provisioned


def test_missing_token_on_tty_fails_closed(seams, monkeypatch, capsys):
    _calls, _root = seams
    monkeypatch.setattr(canary_init.gh, "secret_list", lambda repo: [])
    assert canary_init.main(["--family", "rust"]) == 1
    assert "no RELEASE_TOKEN" in capsys.readouterr().err


def test_notarization_secret_fails_closed(seams, monkeypatch, capsys):
    # OQ3: an ASC key on the repo would arm notarization (Apple's 5-15 min
    # round-trip) on every canary cut — refuse until it is removed.
    calls, _root = seams
    monkeypatch.setattr(
        canary_init.gh, "secret_list", lambda repo: ["RELEASE_TOKEN", "ASC_API_KEY_BASE64"]
    )
    assert canary_init.main(["--family", "rust"]) == 1
    err = capsys.readouterr().err
    assert "forbidden notarization secret" in err and "cert-only" in err
    assert calls.policy_applied == 0


def _declare_signing(root):
    (root / "tests" / "fixtures" / "rust-cli" / ".canary-secrets").write_text(
        "APPLE_CERTIFICATE_P12_BASE64\nAPPLE_CERTIFICATE_PASSWORD\n"
    )


def _auth_dir(tmp_path):
    d = tmp_path / "auth"
    d.mkdir()
    (d / "developerID_application.p12").write_bytes(b"\x01\x02cert")
    (d / "p12_password.txt").write_text("hunter2")
    return d


def test_signing_pair_sourced_and_set_when_missing(seams, monkeypatch, tmp_path, capsys):
    _calls, root = seams
    _declare_signing(root)
    auth = _auth_dir(tmp_path)
    set_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        canary_init.gh,
        "secret_set",
        lambda name, value, repo: set_calls.append((name, value, repo)),
    )
    assert canary_init.main(["--family", "rust", "--auth-dir", str(auth)]) == 0
    assert [(n, r) for n, _v, r in set_calls] == [
        ("APPLE_CERTIFICATE_P12_BASE64", "arthur-debert/release-canary-rust"),
        ("APPLE_CERTIFICATE_PASSWORD", "arthur-debert/release-canary-rust"),
    ]
    values = dict((n, v) for n, v, _r in set_calls)
    assert values["APPLE_CERTIFICATE_PASSWORD"] == "hunter2"
    assert values["APPLE_CERTIFICATE_P12_BASE64"]  # base64 of the .p12, non-empty
    assert "cert-only" in capsys.readouterr().out


def test_signing_pair_kept_when_present(seams, monkeypatch, tmp_path, capsys):
    # Idempotence without local cert material: a converged repo re-runs green
    # on a machine that has no auth dir at all.
    _calls, root = seams
    _declare_signing(root)
    monkeypatch.setattr(
        canary_init.gh,
        "secret_list",
        lambda repo: ["RELEASE_TOKEN", *canary_init.SIGNING_SECRETS],
    )
    monkeypatch.setattr(
        canary_init.gh,
        "secret_set",
        lambda name, value, repo: pytest.fail("must not set when present"),
    )
    assert canary_init.main(["--family", "rust", "--auth-dir", str(tmp_path / "nope")]) == 0
    assert "signing secret(s) present" in capsys.readouterr().out


def test_signing_pair_half_present_is_resourced_whole(seams, monkeypatch, tmp_path):
    # A stale half-pair (password not matching the .p12) would fail every
    # sign-mac run — re-source BOTH.
    _calls, root = seams
    _declare_signing(root)
    auth = _auth_dir(tmp_path)
    monkeypatch.setattr(
        canary_init.gh,
        "secret_list",
        lambda repo: ["RELEASE_TOKEN", "APPLE_CERTIFICATE_P12_BASE64"],
    )
    set_names: list[str] = []
    monkeypatch.setattr(
        canary_init.gh, "secret_set", lambda name, value, repo: set_names.append(name)
    )
    assert canary_init.main(["--family", "rust", "--auth-dir", str(auth)]) == 0
    assert set_names == list(canary_init.SIGNING_SECRETS)


def test_signing_pair_missing_auth_material_fails(seams, monkeypatch, tmp_path, capsys):
    _calls, root = seams
    _declare_signing(root)
    assert canary_init.main(["--family", "rust", "--auth-dir", str(tmp_path / "nope")]) == 1
    err = capsys.readouterr().err
    assert "cannot source the signing pair" in err


def test_piped_pat_routes_through_the_token_verb(seams, monkeypatch):
    calls, _root = seams
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    assert canary_init.main(["--family", "rust"]) == 0
    assert calls.token_installed == ["arthur-debert/release-canary-rust"]


def test_token_verb_failure_propagates(seams, monkeypatch, capsys):
    _calls, _root = seams
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(canary_init, "_install_token", lambda repo: 1)
    assert canary_init.main(["--family", "rust"]) == 1
    assert "installing RELEASE_TOKEN" in capsys.readouterr().err


def test_policy_failure_propagates(seams, monkeypatch, capsys):
    _calls, _root = seams
    monkeypatch.setattr(canary_init, "_apply_policy", lambda dest: 1)
    assert canary_init.main(["--family", "rust"]) == 1
    assert "applying the ruleset" in capsys.readouterr().err


def test_new_family_is_registered_in_the_manifest(seams, monkeypatch, capsys):
    calls, root = seams
    fixtures = root / "tests" / "fixtures" / "vscode-ext"
    fixtures.mkdir()
    (fixtures / ".canary-family").write_text("vscode-ext\n")
    monkeypatch.setattr(canary_init, "_repo_exists", lambda repo: False)
    monkeypatch.setattr(canary_init, "_main_seeded", lambda dest: False)
    assert canary_init.main(["--family", "vscode-ext"]) == 0
    text = (root / "managed-repos.yaml").read_text()
    assert "  vscode-ext: arthur-debert/release-canary-vscode-ext\n" in text
    assert calls.created == ["arthur-debert/release-canary-vscode-ext"]
    assert "registered vscode-ext" in capsys.readouterr().out


def test_new_family_is_registered_before_the_token_install(seams, monkeypatch, capsys):
    # Regression (caught live on the first vscode-ext init): the token install
    # targets the repo via `admin secrets token --repos`, which validates
    # against managed-repos.yaml — so a NEW canary must be registered before
    # the secrets converge runs, or init self-deadlocks on its own registry.
    _calls, root = seams
    fixtures = root / "tests" / "fixtures" / "vscode-ext"
    fixtures.mkdir()
    (fixtures / ".canary-family").write_text("vscode-ext\n")
    monkeypatch.setattr(canary_init, "_repo_exists", lambda repo: False)
    monkeypatch.setattr(canary_init, "_main_seeded", lambda dest: False)
    monkeypatch.setattr(canary_init.gh, "secret_list", lambda repo: [])
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))

    def install_requires_registration(repo):
        text = (root / "managed-repos.yaml").read_text()
        return 0 if f"  vscode-ext: {repo}\n" in text else 1

    monkeypatch.setattr(canary_init, "_install_token", install_requires_registration)
    assert canary_init.main(["--family", "vscode-ext"]) == 0


def test_gh_preflight_failure_is_setup_error(seams, monkeypatch, capsys):
    _calls, _root = seams
    monkeypatch.setattr(canary_init, "_gh_preflight", lambda: False)
    assert canary_init.main(["--family", "rust"]) == 2
    assert "gh auth status" in capsys.readouterr().err


def test_missing_floating_major_is_setup_error(seams, monkeypatch, capsys):
    _calls, _root = seams
    monkeypatch.setattr(canary_init, "_floating_major", lambda: "")
    assert canary_init.main(["--family", "rust"]) == 2
    assert "floating-major" in capsys.readouterr().err
