### Fixed

- `pr review show` now labels each thread's ids — the numeric `comment-id` (the handle `pr resolve-thread` and the new `pr review reply` consume) versus the GraphQL `graphql thread id` — with a one-line legend, so it's unambiguous which to feed back (#687)
- `pr ready` softens the post-flip note: a brief `VALIDATING` status after the draft→ready flip now reads as normal/expected with no further action needed, rather than as an error or a mandatory extra wait (#703)

### Added

- `pr review reply <comment-id> <body>` posts a threaded reply to a review comment — the rationale / push-back path that previously had no `release-core` verb (agents dropped to raw `gh api .../comments/<id>/replies`) (#695)
