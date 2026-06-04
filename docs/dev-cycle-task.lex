The Development Life Cycle Model

This document describes the dev cycle for simple tasks (fit in a single PR)

1. Information Gathering
    The goal of this phase is to ensure that the agent and users are aligned on what is to be done and that the agent has the right information it needs.
    1. Task Description: gh issue OR /handoff artifact OR interactive message.
    2. Agent Contextualization: user reads the description, the related code / resources.
    3. Agent Clarifications: if needed, the agent gathers missing information or things that should be decision points and present them, when possible suggesting its own alternative for how to handle it.
2. Task Implementation:
    Agent works on the task, including writing / improving tests where needed.
    This includes altering an entry to the changelog.
3. PR Shepherding:
    1. Agent opens the PR as draft, linking to the issue if relevant (for #<id> or closes <id>).
    2. Agent ensures the right reviewers are being requested.
    3. Agent waits for reviews + ci checks.
    4. Agent addresses reviews, fixes ci checks if needed, commits and pushes.
    5. Agent ensures that the pr is ready for user validation: all reviews addressed, all ci checks pass and that it is mergeable.
    6. When all are true, flips to PR Ready
4. User Validation
    User will either merge or ask for changes / clarifications. If more work is needed, flip the pr back to draft and only when the needed changes + checks are passing flip to READY for user re-validation

On Re-Viewing

    We need to be more nuanced on this, that is, after a review is addressed and submitted, do we re-ask a review?
    The right answer is: it depends. If the addressing work is about small things (a double allocation, a shell call that is flaky, a better name, etc), the answer is no.
    If the first review is about larger design or algorithm changes, that is, things that the first review is more of a "this really needs to change significantly" way, that is, the addressing commit can have many small to large scale issues, then a second review is welcome, but otherwise not.
