#!/usr/bin/env bash
# generate-pip-index.sh — build a PEP 503 "simple" pip index for the
# release_core wheels, pointing at the EXISTING GitHub release-asset URLs.
#
# The repo is PUBLIC, so the release-asset download URLs need no auth; the
# index is just a thin static directory of <a href> links pip can walk. A
# release-pipeline step regenerates + deploys this to GitHub Pages on each
# release, so consumers/CI can resolve a wheel by version constraint:
#
#   pip install --extra-index-url <pages-url>/simple/ 'release-core>=3,<4'
#
# PyPI stays the PRIMARY index (--extra-index-url, not --index-url), so
# release_core's own deps (click) still resolve from PyPI.
#
# Layout written under $OUTPUT_DIR (PEP 503):
#   simple/index.html                 — root, links to the one project
#   simple/release-core/index.html    — one <a href> per published wheel
#
# RC pollution guard (release#689-class): only NON-prerelease releases are
# enumerated. This mirrors the boot resolver's release filter
# (templates/commons/bin/install-release-core: `select(.draft == false and
# .prerelease == false)`). A `*-release-rc` verify/RC cut must NEVER reach the
# public index.
#
# Env vars:
#   REPO        owner/name to read releases from (default: arthur-debert/release)
#   OUTPUT_DIR  directory to write the index into (default: ./pip-index)
#   GH_TOKEN    used by `gh` (set in CI; optional for a public repo)
#
# Version-agnostic: this generator never parses or assumes the wheel version —
# it just links whatever release_core-<ver>-py3-none-any.whl filenames GitHub
# returns for each non-prerelease release. WS1 stamps real tag-derived versions,
# and the index picks up whatever filenames appear.
#
# Exit codes:
#   0   index written
#   1   no non-prerelease release carries a release_core wheel (nothing to serve)

set -euo pipefail

REPO="${REPO:-arthur-debert/release}"
OUTPUT_DIR="${OUTPUT_DIR:-./pip-index}"

# PEP 503 normalizes the project name (lowercase, runs of [-_.] → single '-').
# release_core → release-core.
PROJECT="release-core"

# The wheel asset name pattern, shared with the boot resolver. Doubled
# backslash so it survives embedding in the jq string literal.
WHEEL_JQ_RE='^release_core-.*\\.whl$'

# Enumerate NON-prerelease releases (newest first), and for each emit the
# release_core wheel asset's name + download URL as "name<TAB>url" lines.
# The prerelease/draft predicate is the SAME one the boot resolver uses —
# this is the single RC-pollution guard, not a second hand-rolled filter.
# per_page=100 (not --paginate): deterministic page, well past any realistic
# release count for one project.
asset_lines="$(
  gh api "repos/${REPO}/releases?per_page=100" --jq "
    .[]
    | select(.draft == false and .prerelease == false)
    | .assets[]?
    | select(.name | test(\"${WHEEL_JQ_RE}\"))
    | \"\\(.name)\t\\(.browser_download_url)\"
  "
)"

if [ -z "${asset_lines}" ]; then
  echo "generate-pip-index: no non-prerelease release of ${REPO} carries a release_core wheel asset — nothing to serve" >&2
  exit 1
fi

# HTML-escape the few characters that matter inside an href / text node. The
# inputs are GitHub-controlled wheel names + URLs (no user free-text), but
# escaping keeps the output well-formed regardless.
html_escape() {
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

mkdir -p "${OUTPUT_DIR}/simple/${PROJECT}"

# Root simple index: one link to the single project. The trailing slash on the
# href is required by PEP 503 (the project URL is a directory).
{
  printf '<!DOCTYPE html>\n'
  printf '<html><head><meta name="pypi:repository-version" content="1.0"><title>Simple index</title></head>\n'
  printf '<body>\n'
  printf '<a href="%s/">%s</a>\n' "${PROJECT}" "${PROJECT}"
  printf '</body></html>\n'
} > "${OUTPUT_DIR}/simple/index.html"

# Project page: one <a href> per wheel, pointing straight at the GitHub
# release-asset URL. Newest-first ordering is preserved from the API; pip
# does its own version sort, so order is cosmetic.
{
  printf '<!DOCTYPE html>\n'
  printf '<html><head><meta name="pypi:repository-version" content="1.0"><title>Links for %s</title></head>\n' "${PROJECT}"
  printf '<body>\n'
  printf '<h1>Links for %s</h1>\n' "${PROJECT}"
  count=0
  while IFS="$(printf '\t')" read -r name url; do
    [ -n "${name}" ] || continue
    e_name="$(printf '%s' "${name}" | html_escape)"
    e_url="$(printf '%s' "${url}" | html_escape)"
    printf '<a href="%s">%s</a><br>\n' "${e_url}" "${e_name}"
    count=$((count + 1))
  done <<EOF
${asset_lines}
EOF
  printf '</body></html>\n'
} > "${OUTPUT_DIR}/simple/${PROJECT}/index.html"

echo "generate-pip-index: wrote ${OUTPUT_DIR}/simple/ index with ${count} wheel(s) for ${PROJECT}" >&2
