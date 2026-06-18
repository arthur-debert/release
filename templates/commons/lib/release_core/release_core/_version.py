"""Build-time version source for the release_core wheel (release#758).

`[tool.hatch.version] source = "code"` evaluates ``VERSION`` here while hatch
builds the wheel, so the published wheel carries the REAL release line
(`release-core 3.1.2`) instead of a frozen literal. The release pipeline's
wheel-build step exports ``TAG=v<version>`` (the tag being cut); we normalize
that semver tag to a PEP 440 / pip-comparable version and fall back to a dev
sentinel for local/editable builds run without that env (`python -m build`, the
test suite from source).

This module is imported ONLY at BUILD time (by hatch, to stamp the wheel — it
reads ``$TAG``) and by the version-stamp test. The normal runtime
``import release_core`` must NEVER reach this module: ``VERSION =
_stamp_version()`` runs at import, and on the build branch it imports
``packaging`` (a BUILD-only dep, not a runtime one — release_core's sole runtime
dep is click). Because ``$TAG`` presence cannot distinguish build from runtime
(``TAG`` is commonly set in CI shells / makefiles), letting the runtime package
import this module would crash a plain ``import release_core`` whenever ``TAG``
is in the env. So ``release_core.__init__`` keeps its OWN local dev sentinel
(``_DEV_VERSION``) rather than importing ``DEV_VERSION`` from here; the two
``0.0.0+dev`` literals are kept in sync by a test. The installed package's
runtime version is whatever ``importlib.metadata.version("release-core")``
returns — see ``release_core.__init__._resolve_version``.

``packaging`` does ALL the PEP 440 normalization (it handles standard
``-rc.N`` / ``-beta.N`` / ``-alpha.N`` prereleases natively); release's bespoke
``-release-rc`` verification suffix gets a one-line adapter. This module stays
SELF-CONTAINED (no relative imports — hatch's code-version source loads the file
in a way where ``from ._x import`` can fail), so it keeps its own
``DEV_VERSION`` literal.
"""

from __future__ import annotations

import os
import re

# 0.0.0+dev: a PEP 440 local-version sentinel. It is unambiguously OLDER than any
# real release (the local segment only breaks ties between equal public
# versions), so a dev build never out-ranks a published wheel in pip's compare.
DEV_VERSION = "0.0.0+dev"


def _stamp_version() -> str:
    """Resolve the wheel version from ``$TAG`` (build time) or the dev sentinel.

    Build time ($TAG set): delegate ALL PEP 440 normalization to
    ``packaging.version.Version`` — it handles ``-rc.N`` / ``-beta.N`` /
    ``-alpha.N`` natively. The ONLY shape it can't know is release's bespoke
    ``-release-rc`` verification suffix (release#663), which gets a one-line
    adapter before packaging validates. A genuinely unknown shape re-raises
    ``InvalidVersion`` — fail-loud on the wheel build, which is what we want.

    Build run without $TAG (`python -m build` from source, the stamp test):
    return the dev sentinel without importing packaging. This module is not
    imported on the normal runtime path at all (see the module docstring).
    """
    tag = os.environ.get("TAG")
    if not tag:
        return DEV_VERSION

    # Build time: $TAG is set; packaging is guaranteed (it's a build dep).
    from packaging.version import InvalidVersion, Version

    v = tag.removeprefix("v")
    try:
        return str(Version(v))
    except InvalidVersion:
        # release's bespoke verification suffix (release#663) is valid semver but
        # not a PEP 440-known prerelease token; map just that one shape, then let
        # packaging validate/normalize (re-raises InvalidVersion = fail-loud on a
        # genuinely unknown shape).
        mapped = re.sub(r"-release-rc(?:\.(\d+))?$", lambda m: f"rc{m.group(1) or 0}", v)
        return str(Version(mapped))


VERSION = _stamp_version()
