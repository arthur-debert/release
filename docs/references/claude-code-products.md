## Claude coding products — final matrix

| Product                    | Execution            | Driver                      | Transport               | Billing                           |
| -------------------------- | -------------------- | --------------------------- | ----------------------- | --------------------------------- |
| **Claude Code (TUI)**      | Local                | Interactive `claude`        | Local pty               | Subscription (Pro/Max) or API key |
| **Claude Code (headless)** | Local                | `claude -p`                 | Subprocess, JSON I/O    | Subscription or API key           |
| **Claude Agent SDK**       | Local (your process) | Python/TS library           | In-process, wraps CLI   | Subscription or API key           |
| **Claude Code Cloud**      | Anthropic sandbox    | Web / mobile / desktop apps | HTTPS to Anthropic      | Subscription only                 |
| **Managed Agents API**     | Anthropic sandbox    | Your code (REST/SDK)        | HTTPS + SSE             | API key only                      |
| **Messages API**           | None (no agent loop) | Your code                   | HTTPS to `/v1/messages` | API key only                      |

### Notes per row

- **Claude Code TUI / headless / Agent SDK** — all three share auth and session state. `ANTHROPIC_API_KEY` in env silently beats OAuth → bills API instead of subscription. Starting June 15 2026, SDK + `-p` usage on subscription draws from a separate Agent SDK credit pool.
- **Claude Code Cloud** — the sandbox you've been using from the app. No public programmatic provisioning API.
- **Managed Agents** — three-object model (Agent / Environment / Session). Beta header `managed-agents-2026-04-01`. Tokens + $0.08/session-hour runtime.
- **Messages API** — raw model access, you write the agent loop yourself.

### Decision shortcuts for your harness

- Want **subscription + programmatic** → Agent SDK, local execution, one process per project.
- Want **cloud sandbox + programmatic** → Managed Agents, accept API billing.
- Want **cloud sandbox + subscription** → no first-party path; stay on the app.
