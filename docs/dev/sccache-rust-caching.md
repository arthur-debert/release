# sccache: shared GCS-backed Rust compiler cache

The Rust and Tauri CI/release lanes run [mozilla/sccache](https://github.com/mozilla/sccache)
as a compiler cache backed by one shared Google Cloud Storage bucket. It
**complements** `Swatinem/rust-cache` — it does not replace it.

## Why

`Swatinem/rust-cache` stores the whole `target/` + cargo registry in the
**GitHub Actions cache**, which is *branch-scoped*: a feature branch can only
read caches it wrote itself or that the default branch wrote. With many
short-lived branches the cache is almost always cold, so every trivial PR pays
a full cold compile.

sccache caches at the **per-compilation-unit** level in a **global** GCS store.
The object key is a content hash of the compiler version, target triple, flags
and preprocessed source — so it is **shared across branches** (the branch is
*not* in the key) and platforms partition automatically (different target →
different key). Cold feature branches get warm dependency caches.

Both run together: Swatinem restores `target/`/registry where it can; sccache
fills the cross-branch gap.

## Infra (GCP project `code-reviews-gh`)

| Resource | Value |
|---|---|
| Bucket | `gs://code-reviews-gh-sccache` (Standard, `us-central1`, uniform access, **soft-delete off**) |
| Lifecycle | **Delete objects at age 30 days** — the only eviction mechanism (sccache has no server-side size cap for cloud backends) |
| Service account | `sccache-ci@code-reviews-gh.iam.gserviceaccount.com` |
| IAM | `roles/storage.objectUser` **scoped to the bucket** (read + write + delete) |
| Key partitioning | `SCCACHE_GCS_KEY_PREFIX=${{ github.repository }}` — one prefix per repo |

### Cost notes

- The 30-day lifecycle is the cost guardrail. `age` counts from object
  *creation*: sccache only writes on a cache miss and never re-touches reused
  entries, so a still-hot entry is deleted once a month and recompiled once —
  acceptable. Shorten the window (14 d) if storage grows; lengthen (45 d) if
  cold rebuilds hurt more than storage.
- **Soft-delete is disabled** on the bucket — GCS now defaults to a 7-day
  soft-delete retention that would roughly double storage cost on a churny
  cache.
- Reads from GCS to GitHub-hosted runners are internet egress (~$0.12/GB).
  Bounded by actual usage + the lifecycle window. Consider a billing budget
  alert on the project if usage climbs.
- Autoclass is intentionally **not** used: this cache is read-heavy /
  write-once, and class demotion would add retrieval/management overhead for no
  benefit.

## Secret

The base64-encoded SA JSON key lives as `SCCACHE_GCS_KEY`:

- **Doppler** `github/prd` — the source of truth.
- **GitHub repo secrets** — distributed by `release-core admin secrets install`,
  which picks up `SCCACHE_GCS_KEY` from the env (optional slot, like
  `NPM_TOKEN`). Source it from Doppler first:

  ```bash
  export SCCACHE_GCS_KEY=$(doppler secrets get SCCACHE_GCS_KEY --plain \
    --project github --config prd)
  release-core admin secrets install            # whole fleet
  ```

## How the lanes wire it

- A composite action `.github/actions/setup-sccache` does all the work: writes
  the key to a temp file, exports `RUSTC_WRAPPER=sccache`, `CARGO_INCREMENTAL=0`
  (sccache cannot cache incremental builds), `SCCACHE_GCS_BUCKET`,
  `SCCACHE_GCS_KEY_PREFIX`, `SCCACHE_GCS_RW_MODE=READ_WRITE` (the default is
  `READ_ONLY`, which silently never populates), `SCCACHE_GCS_KEY_PATH`, and
  installs the sccache binary.
- `setup-rust` calls it, so `rust-ci` / `rust-cli` / `rust-lib` get it for free.
- The Tauri lanes (`tauri-ci`, `tauri-app`, `tauri-e2e`) wire the toolchain
  inline, so they call `setup-sccache` directly after their Swatinem step.
- **Graceful degradation:** an empty `sccache_gcs_key` (fork PRs, repos that
  haven't onboarded the secret) makes the composite a no-op — the build falls
  back to plain Swatinem caching and never fails on a cache problem.

## Enabling it on a consumer

- **arthur-debert/\*** callers using `secrets: inherit` get it automatically
  once the repo has the `SCCACHE_GCS_KEY` secret.
- **Cross-org** callers (`phos-editor/*`, `lex-fmt/*`) must pass it explicitly
  in their `uses:` block, e.g.:

  ```yaml
  secrets:
    sccache_gcs_key: ${{ secrets.SCCACHE_GCS_KEY }}
  ```

## Verifying

Look for the `sccache` install step in the job log, and a non-zero hit rate as
the cache warms across runs. To debug a cold cache, the usual sccache levers
apply (`SCCACHE_LOG=debug`, `SCCACHE_ERROR_LOG`, `sccache --show-stats`); the
top footguns are a missing `READ_WRITE` mode, `CARGO_INCREMENTAL` not `0`, or an
`objectViewer`-only service account.

## Not cached

proc-macro / `bin` / `dylib` / `cdylib` crates, the linker step, and `build.rs`
execution are never cached (they aren't plain compiler invocations) — the final
binary always relinks. Dependency `lib` crates are where the wins are.
