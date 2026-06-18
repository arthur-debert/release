"""Build-time version source for the release_core wheel (release#758).

`[tool.hatch.version] source = "code"` evaluates ``VERSION`` here while hatch
builds the wheel, so the published wheel carries the REAL release line
(`release-core 3.1.2`) instead of a frozen literal. The release pipeline's
wheel-build step exports ``TAG=v<version>`` (the tag being cut); we strip the
leading ``v`` to a PEP 440 / pip-comparable version and fall back to a dev
sentinel for local/editable builds run without that env (`python -m build`, the
test suite from source).

``VERSION`` is evaluated by hatch's code-version source at BUILD time only (it
reads ``$TAG``), so the installed package's version is whatever
`importlib.metadata.version("release-core")` returns at RUNTIME — see
``release_core.__init__._resolve_version``, the single runtime source for
``release_core.__version__``. ``DEV_VERSION`` is a plain constant that is ALSO
imported at runtime by ``release_core.__init__`` as the source-checkout
fallback when no installed metadata is present; the import is harmless (a cheap
string), so it stays here rather than being duplicated.
"""

from __future__ import annotations

import os

# 0.0.0+dev: a PEP 440 local-version sentinel. It is unambiguously OLDER than any
# real release (the local segment only breaks ties between equal public
# versions), so a dev build never out-ranks a published wheel in pip's compare.
DEV_VERSION = "0.0.0+dev"

VERSION = (os.environ.get("TAG") or "").removeprefix("v") or DEV_VERSION
