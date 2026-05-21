# Pull Request

## Summary

<!-- 1-3 sentences: what changed and why. -->

## Checklist

- [ ] Changelog `Unreleased` section updated (or chore/docs-only)
- [ ] Project umbrella check passes locally — `bin/check` (canonical: composes `bin/check-fmt` + `bin/check-lint` + `bin/check-tests`). Bare equivalent: `cargo fmt --check && cargo clippy --all-targets --all-features -- -D warnings && cargo nextest run --all-features`.
- [ ] Tests added or updated for behavior changes

## Notes for reviewers

<!-- Optional: context to help triage Copilot's review faster. -->
