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

import release_core
from release_core import _version


def test_dev_sentinel_is_pep440_local_version():
    # 0.0.0+dev: a local-version segment, so it ranks BELOW any real release in a
    # pip compare (the build-time fallback must never out-rank a published wheel).
    assert _version.DEV_VERSION == "0.0.0+dev"


def test_build_version_strips_leading_v(monkeypatch):
    # The hatch code-version source evaluates release_core/_version.py:VERSION at
    # build time; it reads $TAG and strips the leading `v` to a PEP 440 version.
    monkeypatch.setenv("TAG", "v3.4.2")
    assert importlib.reload(_version).VERSION == "3.4.2"


def test_build_version_strips_exactly_one_leading_v(monkeypatch):
    # removeprefix("v") drops exactly ONE leading `v`, not all of them — so a
    # `vv`-prefixed tag keeps the second `v` rather than being over-stripped.
    monkeypatch.setenv("TAG", "vv3.1.0")
    assert importlib.reload(_version).VERSION == "v3.1.0"


def test_build_version_falls_back_without_tag(monkeypatch):
    monkeypatch.delenv("TAG", raising=False)
    assert importlib.reload(_version).VERSION == _version.DEV_VERSION


def test_runtime_version_uses_installed_metadata(monkeypatch):
    # When the package IS installed, __version__ reflects the wheel's stamped
    # version (importlib.metadata), not a literal.
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "7.8.9")
    assert release_core._resolve_version() == "7.8.9"


def test_runtime_version_falls_back_for_source_checkout(monkeypatch):
    # bin/release-core's PYTHONPATH shim / the test suite from source: the package
    # metadata is absent, so fall back to the dev sentinel (no crash, no literal).
    def _missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _missing)
    assert release_core._resolve_version() == _version.DEV_VERSION
