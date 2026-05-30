Unified Lint Gate:
one runner, dogfooded by the producer

Source location: arthur-debert/release, path docs/proposals/. Motivated by the fleet re-sync (release#348/#353), where the same "is this shell, does it pass?" question was answered three different ways and a release-produced file (the gh-task-status shim) failed a gate release also produces. Companion: docs/distribution.lex.

1. The problem

    The lint gate is the single most brittle part of the release↔consumer flow, and the brittleness is structural, not incidental.

    1.1. The gate is three implementations that drift

        "Lint the shell files and pass" is currently decided in three places that disagree:

        - pre-commit (the composed lefthook.yml) selects files by `glob` + `exclude`. Brittle: it lints non-shell files (the Python shim, Dockerfiles) and the `exclude` is not honoured the same way across lefthook invocation modes (staged commit vs `--all-files`).
        - CI (the reusable workflow) selects by shebang detection. Robust — it correctly skips the shim.
        - release-verify-fleet runs `lefthook ... --all-files`, a third mode.

        So a file passes one implementation and fails another. Every per-file fix (`**/gh-task-status`, a Dockerfile exclude, …) patches one surface while the others stay wrong. This is the exact "parallel implementations drift" failure the project's shared-workflow rule exists to prevent — applied everywhere except the gate.

    1.2. The pre-flight is not faithful

        release-verify-fleet is meant to answer "would this break consumers?" but it runs the gate in a mode consumers don't, so its green ≠ the consumer's green. The shim break surfaced *after* the v2 advance and 15 PRs instead of before. A pre-flight that does not run exactly what production runs is theatre.

    1.3. The producer never lints its own output with the shipped config

        Release produces the distributed files (under templates/) AND the gate config (commons), in the same repo, and never introduces them to each other. The files release ships are linted for the first time by a consumer's CI, one repo at a time. Linting is the most deterministic thing here; it should be boring.

2. The invariant we were missing

    Release produces the files and the gate config. Therefore release must lint its own output with that config, and it must pass — by construction. Close that loop and the whole class of "a distributed file breaks consumers" disappears at the source.

3. Design

    3.1. One runner, content-based, in a script

        The gate logic moves out of lefthook YAML `run:` strings into a single distributed runner script (shipped in commons). It selects files by *content*, never path globs: shell = a shell shebang (or `.sh`/`.bash`), so non-shell files (the shim, Dockerfiles, vendored tools) fall out structurally and forever. The runner is a real script — testable, mode-invariant (it filters whatever file list it is handed), and the one implementation of the rule. (The CI workflow already does shebang detection; we promote that into the shared runner rather than keep a second copy.)

    3.2. One invocation everywhere

        pre-commit (lefthook), the CI reusable workflow, and release-verify-fleet all call the same runner the same way. CI stops reimplementing shellcheck. The pre-flight becomes faithful: its result equals the consumer's.

    3.3. Release dogfoods the shipped gate

        A release CI job release-syncs the templates into a fixture consumer (real .release/ + symlinks + shim layout) and runs the canonical gate over it. Anything that would break a consumer breaks release CI first — the shim SC1071 and the setup-dev-env SC2015 would both have been caught here, at the source, instead of at N consumers.

4. First cut: shellcheck

    Shellcheck is where the pain is (the shim, Dockerfiles, setup-dev-env). Unify it first, prove the pattern on the fleet, then migrate markdownlint / yamllint / prettier the same way.

    Steps:
        - Add `templates/commons/bin/check-shell`: takes file paths, keeps only shell (shebang or extension), runs `shellcheck --severity=info` on those. The one place "shellcheck the shell" lives.
        - Rewire the commons shellcheck lefthook step to call `check-shell {staged_files}` — no glob `exclude` gymnastics; the script does selection. Mode-invariant.
        - Point the CI reusable workflow's shellcheck at the same runner.
        - Add the release dogfood job: sync a fixture consumer, run the gate.
        - BATS: the gate skips the shim + a Dockerfile, still flags a real shell error.

5. Decisions resolved

    - Runner form: a distributed runner script (gate logic in shell, not YAML run-strings).
    - Dogfood: release-sync into a fixture consumer and run the gate there (tests the real synced layout — symlinks, the shim, the composed gate), not templates/** in place.
    - Scope: shellcheck first, then fold in the other linters the same way.

6. Out of scope (for the first cut)

    - Migrating markdownlint / yamllint / prettier into the runner (follow-ups, same shape).
    - Per-kind linters (cargo, eslint) — they stay as their own fragment steps; the runner is for the commons content-typed linters.
    - The markdownlint test-fixtures gap (release#353) — folded in when markdownlint migrates.
