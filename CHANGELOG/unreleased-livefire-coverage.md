### Fixed

- `release-core coverage` no longer forwards `--coverage` to a tree-sitter grammar's `tree-sitter test` script (the tree-sitter CLI rejects it with `unexpected argument '--coverage'`); the tree-sitter Kind now falls through to the no-coverage path, consistent with nvim-plugin (#696)
- `release-core coverage` on a Kind with no coverage-capable toolchain (nvim-plugin, tree-sitter) now prints a clear, expected "this Kind has no coverage tool" notice that points at `release-core how-to`, instead of a terse line that read like a crash (behavior unchanged — still exit 1) (#701)
- `release-core coverage` now suppresses the verbose build/test stream by default and prints only the trailing per-module summary table; a `--verbose`/`--raw` flag restores the full live stream, and on failure the whole captured output is shown for diagnosis (#694)
