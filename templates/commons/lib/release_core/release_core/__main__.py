"""``python -m release_core`` — the same CLI as the ``release-core`` console-script.

Exists so a release_core verb can re-invoke the CLI as a subprocess WITHOUT a
PATH lookup: ``[sys.executable, "-m", "release_core", ...]`` is guaranteed to
run the same interpreter, environment, and package as the calling process —
wheel install and in-checkout launcher alike (the launcher pins PYTHONPATH to the
checkout lib, which propagates to the child). A bare-name ``release-core``
spawn resolves through PATH instead, where a nested in-checkout launcher cannot
re-resolve its deps from the isolated venv (release#534).
"""

import sys

from .cli_entry import main

if __name__ == "__main__":
    sys.exit(main())
