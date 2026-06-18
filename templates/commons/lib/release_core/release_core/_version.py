"""Build-time version source for the release_core wheel (release#758).

`[tool.hatch.version] source = "code"` evaluates ``VERSION`` here while hatch
builds the wheel, so the published wheel carries the REAL release line
(`release-core 3.1.2`) instead of a frozen literal. The release pipeline's
wheel-build step exports ``TAG=v<version>`` (the tag being cut); we normalize
that semver tag to a PEP 440 / pip-comparable version and fall back to a dev
sentinel for local/editable builds run without that env (`python -m build`, the
test suite from source).

Hatch *reads* ``VERSION`` at BUILD time to stamp the wheel (it reads ``$TAG``).
The ``VERSION = …`` assignment below also runs whenever this module is imported
at runtime, but that runtime value is NOT used: the installed package's version
is whatever `importlib.metadata.version("release-core")` returns — see
``release_core.__init__._resolve_version``, the single runtime source for
``release_core.__version__``. ``DEV_VERSION`` is a plain constant that is ALSO
imported at runtime by ``release_core.__init__`` as the source-checkout
fallback when no installed metadata is present; the import is harmless (a cheap
string), so it stays here rather than being duplicated.

``packaging`` does ALL the PEP 440 normalization (it handles standard
``-rc.N`` / ``-beta.N`` / ``-alpha.N`` prereleases natively). Its import is
GUARDED to the build-time branch (``$TAG`` set) because ``packaging`` is a
BUILD dependency (see ``[build-system] requires``), NOT a runtime one —
release_core's only runtime dep is click. The runtime import of this module
(for ``DEV_VERSION``) must never reach the ``packaging`` import.
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

    Runtime / source / editable build ($TAG unset): return the dev sentinel.
    VERSION's value is unused at runtime (only DEV_VERSION is consumed by
    __init__), so we never import packaging here.
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
