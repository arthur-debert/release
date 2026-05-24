# Pull Request

## Summary

<!-- 1-3 sentences: what changed and why. -->

## Checklist

- [ ] Changelog updated (`bin/changelog add <slug>` to add a `CHANGELOG/unreleased-*.md` fragment; `CHANGELOG.md` is generated) — or chore/docs-only
- [ ] Project umbrella check passes locally — `bin/check` (format, lint, typecheck, unit tests)
- [ ] If touching `package.json` `contributes` (commands, languages, grammars, themes), the new surface is covered by an integration test or smoke test
- [ ] If touching the activation events / `main` entry, verified the extension still activates in a fresh VS Code Insiders window

## Notes for reviewers

<!-- Optional: context to help triage Copilot's review faster. -->
