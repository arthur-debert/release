"""Throwaway sample for the gh-PR-tooling live-verification probe (release#337).

NOT real code and NOT meant to merge. This file exists only to give the review
bots (Copilot, Gemini) a small diff to comment on, so we can exercise
gh-task-status against real review/thread/reaction payloads and capture them as
test fixtures. The PR is closed and this branch deleted once payloads are saved.
"""


def average(values, count):
    total = 0
    for value in values:
        total += value
    return total / count
