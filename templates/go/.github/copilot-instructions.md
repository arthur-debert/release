# Copilot Instructions

This is a Go project (CLI, server, or module).

## Before suggesting a fix

- Run the project's umbrella check — `bin/check` (which dispatches to
  `bin/check-fmt`, `bin/check-lint`, `bin/check-tests`). If the repo
  predates the Component-model adoption, fall back to
  `gofmt -l . && go vet ./... && go test ./...` (plus
  `golangci-lint run` if `.golangci.yml` is present). CI runs the
  same; if your suggestion doesn't pass, it won't merge — check
  `.github/workflows/` for the source of truth.
- Never propose changes that leave tests failing.
- Update the changelog's `Unreleased` section for user-visible changes
  (`CHANGELOG_UNRELEASED.md` if the project has one, otherwise the
  `## [Unreleased]` section of `CHANGELOG.md`).

## Style and scope

- Keep changes minimal. Don't add features, refactor, or introduce abstractions
  beyond what the task requires.
- No backwards-compatibility hacks: no `// removed` comments, no renaming unused
  vars to `_`, no shim packages. If something is unused, delete it.
- No fallbacks, defaults, or feature flags unless the PR explicitly asks for them.
- Default to no comments. Well-named identifiers carry the *what*. Reserve
  comments for non-obvious *why* (hidden constraint, workaround, surprising
  invariant). Export comments are the exception — `golint` / `revive` will
  require them on exported identifiers, but keep them tight.
- Trust internal code and framework guarantees. Only validate at system
  boundaries (user input, external commands, filesystem entry, network).
- Prefer the standard library. Reach for a third-party module only when the
  stdlib equivalent would be substantially worse.

## Go-specific

- `gofmt` / `goimports` style is non-negotiable — CI rejects any file that
  doesn't round-trip through the formatter. Don't argue with the formatter.
- Errors flow up via the `error` return — never `panic` outside `main` or
  truly unrecoverable invariant violations.
- New exported APIs need a brief godoc comment on the identifier; unexported
  helpers usually don't.
- Tests live in `*_test.go` next to the code they exercise. Use table-driven
  tests when there are more than ~3 cases. Table entries are named via the
  `name` field, and the test loop calls `t.Run(tc.name, …)` so failing
  subtests are individually addressable.
