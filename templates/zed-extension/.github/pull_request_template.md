# Pull Request

## Summary

<!-- 1-3 sentences: what changed and why. -->

## Checklist

- [ ] Changelog `Unreleased` section updated (or chore/docs-only)
- [ ] Project umbrella check passes locally — `bin/check` (canonical: composes `bin/check-fmt` + `bin/check-lint` + `bin/check-tests`). Bare equivalent: `cargo fmt --all -- --check && cargo clippy --release --target wasm32-wasip2 --all-features -- -D warnings && bats test/` (the canonical `bin/check-tests` discovers the consumer's bats layout and skips with a notice if none is present).
- [ ] Version bumped in BOTH `extension.toml` AND `Cargo.toml` (zed-extension dual-version sync — they must match) for any user-visible change
- [ ] Tests added or updated for behavior changes

## Notes for reviewers

<!-- Optional: context to help triage Copilot's review faster. -->
