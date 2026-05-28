Documentation Index

This is the index of the documentation for the project. It provides an overview of the available topics and sections. For domain vocabulary see CONTEXT.md at the repo root.

1. Status and Roadmap [status.lex]: directional roadmap and current project state.
2. How to Work on Release [working-on-release.lex]: the development cycle for making changes to release and rolling them out to consumers.
3. Managed Repos [managed-repos.lex]: the full list of consumer repos.
4. release-sync: how the injection mechanism works so that file-based capabilities in release are available in all consumer repos.

Foundational Design Documents

5. Development Flow [foundational-gh-layer.lex]: the PR review lifecycle and its current friction points.
6. Architecture Review [foundational-arch-review.lex]: notes on decomposing build, pack, sign, and publish.
7. Secret Management [foundational-secret-management.lex]: plans for Doppler-based secret organization.
8. Internal Tooling [foundational-on-internal-tools.lex]: moving internal scripts to Python with shared primitives.
9. Codebase Walkthrough [current-walkthrough.lex]: agent-generated overview of the repo structure.

Architecture Decision Records

    See docs/adr/ for recorded decisions. Current ADRs:
    - 0001: release-sync uses a build directory with symlinks.
