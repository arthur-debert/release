# Pull Request

## Summary

<!-- 1-3 sentences: what changed and why. -->

## Checklist

- [ ] Changelog updated (`changelog add <slug>` to add a `CHANGELOG/unreleased-*.md` fragment; `CHANGELOG.md` is generated) — or chore/docs-only
- [ ] Project umbrella check passes locally — `bin/check` (covers
      both the frontend half: prettier + eslint + typecheck + unit
      tests, AND the rust half: cargo fmt + clippy + tests in
      `src-tauri/`)
- [ ] Tests added or updated for behavior changes (frontend unit
      tests via vitest; rust tests via `cargo test`; Playwright for
      e2e)

## Notes for reviewers

<!-- Optional: context to help triage Copilot's review faster. -->
