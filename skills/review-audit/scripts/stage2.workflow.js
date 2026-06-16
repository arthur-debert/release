// Stage 5 — the LLM-judging tier, as a `release` Workflow fan-out.
//
// Repo-agnostic. One agent judges ~4 reviewed PRs, reading the de-noised
// slim/ files produced by stages 1-4 and emitting SCHEMA-ENFORCED verdicts
// per finding / per reviewer / per PR. Verdicts land in verdicts/pr-*.json,
// which stages 6 (aggregate.py) and 7 (converge.py) join with the mechanical
// metrics.
//
// Configure via the workflow args object (no hardcoded repo/paths):
//   { dir: '/abs/path/to/analysis/reviews',   // REQUIRED: the output dir
//     reviewed: [60, 61, ...],                 // optional: PR numbers to judge
//     reviewers: ['copilot','gemini','coderabbit'], // optional: enum of bots
//     batch: 4 }                               // optional: PRs per agent
//
// If `reviewed` is omitted, the reviewed-PR list is DERIVED by scanning
// dir/slim/*.json for files whose `reviewers` array is non-empty — so it
// adapts to any repo instead of carrying a hardcoded fallback.

import fs from 'node:fs'

export const meta = {
  name: 'review-audit-judging',
  description: 'Per-PR LLM judgment of bot-review value, criticality & convergence',
  phases: [{ title: 'Judge', detail: 'one agent per ~4 reviewed PRs' }],
}

const cfg = (typeof args === 'object' && args && !Array.isArray(args)) ? args : {}
const DIR = cfg.dir || process.env.REVIEW_AUDIT_DIR
if (!DIR) throw new Error('review-audit: pass args.dir (the analysis/reviews output dir) or set REVIEW_AUDIT_DIR')
// Fall back to defaults only when the provided value is actually usable:
// reviewers must be a non-empty array (an empty enum makes the schema reject
// every finding); batch must be a positive integer (<=0 loops forever below).
const REVIEWERS =
  Array.isArray(cfg.reviewers) && cfg.reviewers.length
    ? cfg.reviewers
    : ['copilot', 'gemini', 'coderabbit']
const BATCH =
  Number.isInteger(cfg.batch) && cfg.batch > 0 ? cfg.batch : 4

// Derive the reviewed-PR list from the slim files unless given explicitly.
let reviewed = cfg.reviewed
if (Array.isArray(args) && args.length) reviewed = args
else if (typeof args === 'string' && args.trim().startsWith('[')) reviewed = JSON.parse(args)
if (!reviewed || !reviewed.length) {
  const slimDir = `${DIR}/slim`
  if (!fs.existsSync(slimDir)) {
    throw new Error(
      `review-audit: ${slimDir} not found — run the mechanical tier first ` +
        `(extract.py -> enrich.py -> finalize.py with the same --dir), or pass args.reviewed.`,
    )
  }
  reviewed = fs.readdirSync(slimDir)
    .filter((f) => /^pr-\d+\.json$/.test(f))
    .map((f) => ({ n: parseInt(f.match(/\d+/)[0], 10), p: `${slimDir}/${f}` }))
    .filter(({ p }) => (JSON.parse(fs.readFileSync(p, 'utf8')).reviewers || []).length)
    .map(({ n }) => n)
    .sort((a, b) => a - b)
}

const batches = []
for (let i = 0; i < reviewed.length; i += BATCH) batches.push(reviewed.slice(i, i + BATCH))
log(`${reviewed.length} reviewed PRs -> ${batches.length} agents (~${BATCH}/agent)`)

const FIELD_GUIDE = `
Each slim file has: title, body, author, latency fields, n_commits, commit_dates,
reviews[] (top-level bot reviews: reviewer, round, state, body) and
threads[] (inline comments: reviewer, round, path, line, diff_hunk = the exact code
the comment is about, comments[] = the conversation, plus signals you MUST use:
 - round: which review pass (1 = first). Map rounds in time order.
 - resolved / resolved_by: GitHub thread marked resolved (strong "actioned" signal).
 - gh_outdated / outdated: the commented code changed after the comment (author touched it).
 - author_replied: PR author replied in-thread.
 - code_changed_after: a commit landed after the comment.
 - bot_followup_rounds: rounds in which the SAME bot re-commented on this thread.
 - n_replies.
CodeRabbit puts most signal in review BODIES (reviews[].body) and self-marks
"Addressed in <sha>" — treat that as actioned=changed. Copilot puts findings both
in review bodies and inline threads. Gemini is mostly inline.`

const RUBRIC = `
For EVERY substantive bot comment (inline thread OR a distinct point in a review body)
emit a finding:
 - category: correctness|bug|security|perf|maintainability|test|docs|style|nit|noise|false_positive
 - severity: blocker|important|minor|nit|noise
 - true_positive: yes|no|unclear  (is the claim technically correct, judging from diff_hunk + context?)
 - actioned: changed|acknowledged|dismissed|ignored|unclear  (resolved/outdated/replies/"Addressed"=changed)
 - novelty: unique | caught_elsewhere | unclear  (caught_elsewhere = a passing test, the repo's linter/CI,
            the compiler/type-checker, or the OTHER bot on this same PR would already have flagged it)
 - value: 0-3 net usefulness of THIS comment (0=noise/wrong, 1=trivial nit, 2=useful, 3=real bug/design save)
 - gist: <=15 words of what it said.

CONVERGENCE (per reviewer, per_reviewer[]): order that reviewer's rounds by time.
 - one_shot: reviewed only once.
 - converging: later rounds follow up on code the author changed for earlier feedback,
   and/or earlier threads resolve/outdate, with FEWER & narrower new issues each round.
 - churning: later rounds keep raising NET-NEW unrelated issues on code not touched since,
   or re-raise resolved points — the reviewer never settles.
 - mixed / na.
 Give convergence_evidence (<=25 words) citing rounds/resolved/outdated.
 signal = # findings with value>=2 ; noise = # findings with value<=0 (or false_positive).
 net_value 0-3 = this reviewer's overall worth ON THIS PR (volume-adjusted: lots of nits = low).`

const PR_ITEM = {
  type: 'object',
  additionalProperties: false,
  required: ['number', 'findings', 'per_reviewer', 'best_catch', 'worst_noise', 'pr_summary'],
  properties: {
    number: { type: 'integer' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['reviewer', 'gist', 'category', 'severity', 'true_positive', 'actioned', 'novelty', 'value'],
        properties: {
          reviewer: { enum: REVIEWERS },
          round: { type: ['integer', 'null'] },
          path: { type: ['string', 'null'] },
          gist: { type: 'string' },
          category: { enum: ['correctness', 'bug', 'security', 'perf', 'maintainability', 'test', 'docs', 'style', 'nit', 'noise', 'false_positive'] },
          severity: { enum: ['blocker', 'important', 'minor', 'nit', 'noise'] },
          true_positive: { enum: ['yes', 'no', 'unclear'] },
          actioned: { enum: ['changed', 'acknowledged', 'dismissed', 'ignored', 'unclear'] },
          novelty: { enum: ['unique', 'caught_elsewhere', 'unclear'] },
          value: { type: 'integer', minimum: 0, maximum: 3 },
        },
      },
    },
    per_reviewer: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['reviewer', 'rounds', 'convergence', 'signal', 'noise', 'net_value'],
        properties: {
          reviewer: { enum: REVIEWERS },
          rounds: { type: 'integer' },
          convergence: { enum: ['one_shot', 'converging', 'churning', 'mixed', 'na'] },
          convergence_evidence: { type: 'string' },
          signal: { type: 'integer' },
          noise: { type: 'integer' },
          net_value: { type: 'integer', minimum: 0, maximum: 3 },
        },
      },
    },
    best_catch: { type: 'string' },
    worst_noise: { type: 'string' },
    pr_summary: { type: 'string' },
  },
}

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['verdicts'],
  properties: { verdicts: { type: 'array', items: PR_ITEM } },
}

phase('Judge')
const results = await parallel(batches.map((b) => () => {
  const files = b.map((n) => `${DIR}/slim/pr-${n}.json`).join('\n')
  const prompt =
`You are auditing the VALUE of automated PR code reviews (${REVIEWERS.join(', ')})
on a software repository. Judge what the bots actually contributed.

Read these slim PR files (use the Read tool on each):
${files}

${FIELD_GUIDE}

${RUBRIC}

Be skeptical and concrete. A comment is only "value 3" if ignoring it would ship a real
bug/regression/security/perf/design problem. Boilerplate, restating the diff, speculative
"consider", and style nits on conventional code are noise (value 0-1). Use diff_hunk to
check whether the bot's claim is actually true (true_positive); judge novelty against what
this repo's own linter / CI / type-checker (not a specific language's tool) would already catch.

After analyzing, for EACH PR write its verdict object to ${DIR}/verdicts/pr-<number>.json
(use Bash: mkdir -p ${DIR}/verdicts first). Then return {verdicts: [...]} containing the verdict objects for
all ${b.length} PRs (numbers ${b.join(', ')}), matching the schema exactly.`
  return agent(prompt, { label: `prs ${b[0]}..${b[b.length - 1]}`, schema: SCHEMA, agentType: 'general-purpose' })
}))

const flat = results.filter(Boolean).flatMap((r) => r.verdicts || [])
log(`collected ${flat.length} PR verdicts`)
return flat
