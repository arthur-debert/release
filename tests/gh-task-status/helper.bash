# Shared setup for tests/gh-task-status/*.bats.
#
# These are CONTRACT tests: the offline surface of the gh-task-status shim —
# arg handling, exit codes, usage text. They never touch gh or the network
# (the live lifecycle behaviour is covered by pytest over fixtures and, end to
# end, by the live-verification phase, issue #337).

BIN="$BATS_TEST_DIRNAME/../../bin"
