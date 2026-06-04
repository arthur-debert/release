The Development Life Cycle Model

This document describes the dev cycle for simple tasks (fit in a single PR)

1. Information Gathering
    The goal of this phase is to ensure that agent and users are aligned as what is to be done and that agent has the right information it needs 
    1. Task Description: gh issue OR /handoff artifact OR interactive message.
    2. Agent Contextualization: user reads the description, the related code / resources. 
    3. Agent Clarifications: if needed, the agent gathers missing information or things that should be decision points and present them, when possible suggesting it's own alternative for how to handle it. 
2. Task Implementation: 
    Agent works on the task , including writing / improving tests where needed.
    This includes altering a entry to the changelog.
3. PR Shepherding:
    1. Agent opens the PR as draft, Link to issue if relevant (for #<id> or closes <id> )
    2. Agent ensure the right reviewers are being requested.
    3. Agent wait for reviews + ci checks.
    4. Agent addresses reviews, fixes ci checks if needed, commit and push.
    5. Agent ensures that pr is ready for user validation: all reviews addresses, all ci checks pass and that is mergeable.
    6. When all are true, flips to PR Ready
4. User Validation
    User will either merge or ask for changes / clarifications. If more work is needed, flit the pr back to draft and only when the needed changes + checks are passing flip to READY for user re-validation

On Re-Viewing

    We need to be more nuanced o this, that is after a review is addressed and submitted, do we re-ask a review? 
    The right answer is depends. If the addressing work is about small things (a double allocation, a shell call that is flaky, a better name, etc), the answer is no.
    If the first review is about larger design or algorithm changes, that is, things that the first review is more of a "this really needs to change significantly" way, that is, the addressing commit an have many small to large scale issues, then a second review is welcome, but otherwise not.
