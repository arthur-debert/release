"""Version stamping (release#758): the wheel version is stamped from the release
tag at build time, and the runtime ``__version__`` derives from the installed
package metadata (with a dev fallback for source checkouts) — never a hardcoded
literal.

The BUILD-time stamping (``$TAG`` → wheel version) is exercised by the release
pipeline + the install-release-core bats suite, not unit-testable here. What we
pin is the RUNTIME resolution contract, which both ``__version__`` attrs and
``release-core --version`` rely on, plus the ``_version.VERSION`` env logic the
hatch code-version source evaluates at build time.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

# Deterministic collection: importing release_core._version below runs
# _stamp_version() at module load, which on an ambient $TAG would take the
# build-time path and could raise InvalidVersion at COLLECTION time (the
# suite-side of the runtime-import bug). Clear TAG before that import; the tests
# that exercise the build path set TAG explicitly via monkeypatch + reload.
# (release#758)
os.environ.pop("TAG", None)

import pytest  # noqa: E402
import release_core  # noqa: E402
from packaging.version import InvalidVersion  # noqa: E402
from release_core import _version  # noqa: E402


def test_dev_sentinel_is_pep440_local_version():
    # 0.0.0+dev: a local-version segment, so it ranks BELOW any real release in a
    # pip compare (the build-time fallback must never out-rank a published wheel).
    assert _version.DEV_VERSION == "0.0.0+dev"


def test_build_version_strips_leading_v(monkeypatch):
    # The hatch code-version source evaluates release_core/_version.py:VERSION at
    # build time; it reads $TAG and normalizes it to a PEP 440 version.
    monkeypatch.setenv("TAG", "v3.4.2")
    assert importlib.reload(_version).VERSION == "3.4.2"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        # Final release: leading `v` stripped, already PEP 440.
        ("v3.1.0", "3.1.0"),
        # Standard semver prereleases packaging normalizes natively
        # (`-rc.N`→`rcN`, `-beta.N`→`bN`, `-alpha.N`→`aN`).
        ("v3.1.0-rc.1", "3.1.0rc1"),
        ("v3.1.0-beta.2", "3.1.0b2"),
        ("v3.1.0-alpha.3", "3.1.0a3"),
        # release's bespoke verification suffix (release#663): the one-line
        # adapter maps it to a PEP 440 rc before packaging validates.
        ("v3.1.0-release-rc", "3.1.0rc0"),
        ("v3.1.0-release-rc.5", "3.1.0rc5"),
    ],
)
def test_build_version_normalizes_prerelease_tags(monkeypatch, tag, expected):
    # Build time ($TAG set): packaging does all PEP 440 normalization; the
    # bespoke `-release-rc` suffix gets a narrow adapter. Pin the wheel-version
    # output for every tag shape the pipeline cuts (release#758 round-4 catch:
    # these semver tags are NOT valid PEP 440 and would fail the wheel build).
    monkeypatch.setenv("TAG", tag)
    assert expected == importlib.reload(_version).VERSION


def test_build_version_rejects_unknown_shape(monkeypatch):
    # A genuinely unknown shape is NOT silently passed through: the adapter
    # leaves it alone and packaging re-raises InvalidVersion — fail-loud on the
    # wheel build, never a bogus version.
    monkeypatch.setenv("TAG", "v3.1.0-gibberish.x")
    with pytest.raises(InvalidVersion):
        importlib.reload(_version)


def test_build_version_falls_back_without_tag(monkeypatch):
    monkeypatch.delenv("TAG", raising=False)
    assert importlib.reload(_version).VERSION == _version.DEV_VERSION


def test_runtime_version_uses_installed_metadata(monkeypatch):
    # When the package IS installed, __version__ reflects the wheel's stamped
    # version (importlib.metadata), not a literal. The stub asserts the queried
    # dist name is exactly "release-core" (hyphen, per pyproject `name`) — a
    # regression to "release_core" (underscore) would raise PackageNotFoundError
    # and silently fall back to the dev sentinel, which this guards against.
    def _ver(name):
        assert name == "release-core"
        return "7.8.9"

    monkeypatch.setattr(importlib.metadata, "version", _ver)
    assert release_core._resolve_version() == "7.8.9"


def test_runtime_version_falls_back_for_source_checkout(monkeypatch):
    # bin/release-core's PYTHONPATH shim / the test suite from source: the package
    # metadata is absent, so fall back to the dev sentinel (no crash, no literal).
    def _missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _missing)
    assert release_core._resolve_version() == _version.DEV_VERSION


def test_dev_sentinels_stay_in_sync():
    # __init__ keeps its OWN local _DEV_VERSION literal (so a normal import never
    # touches _version.py — see test below). Guard the two literals against drift.
    assert release_core._DEV_VERSION == _version.DEV_VERSION


def test_import_release_core_is_safe_with_TAG_set():
    # Regression (release#758): a plain `import release_core` with $TAG set in the
    # env must NOT run _version.py's build-time path. _version imports packaging
    # (a build-only dep) on the TAG branch, so importing it at runtime would crash
    # `import release_core` whenever TAG is present (CI shells, makefiles) — and
    # even with packaging installed, a non-PEP440 TAG would raise InvalidVersion.
    # Assert the import is clean AND that _version was never dragged in.
    pkg_parent = Path(release_core.__file__).resolve().parent.parent
    env = {
        **os.environ,
        "TAG": "not-a-version-!!",
        "PYTHONPATH": os.pathsep.join([str(pkg_parent), os.environ.get("PYTHONPATH", "")]).rstrip(
            os.pathsep
        ),
    }
    code = (
        "import sys; import release_core; "
        "assert 'release_core._version' not in sys.modules, "
        "sorted(m for m in sys.modules if 'release_core' in m); "
        "assert 'packaging' not in sys.modules, 'packaging dragged in at import'; "
        "print(release_core.__version__)"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"import crashed with TAG set:\n{r.stderr}"
    # No installed metadata in this tree → the runtime fallback sentinel.
    assert r.stdout.strip() == release_core._DEV_VERSION
