#!/usr/bin/env bash
# Install NVIDIA BioNeMo Agent Toolkit skills for use alongside Biologix.
#
# The BioNeMo Agent Toolkit publishes agent skills that call BioNeMo NIMs
# directly. Biologix's own MCP server (biologix-mcp) exposes the polymer
# pipeline. The two are complementary: install these skills for direct model
# access, and register the MCP server for the pipeline that enforces the
# repeat-unit, oligomer and evidence-tier discipline.
#
# This script only installs skills. It starts no NIM container and needs no GPU.
#
# Usage:
#   scripts/install_bionemo_skills.sh [--agent claude-code|codex] [--list]
set -euo pipefail

REPO="NVIDIA-BioNeMo/bionemo-agent-toolkit"
AGENT="claude-code"
SKILLS=(boltz2-nim diffdock-nim molmim-nim genmol-nim nvmolkit)
LIST=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent) AGENT="$2"; shift 2 ;;
    --list)  LIST=1; shift ;;
    --skill) SKILLS=("$2"); shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if ! command -v npx >/dev/null 2>&1; then
  cat >&2 <<'EOF'
npx was not found on PATH. The skills CLI is distributed via npm, so Node.js is
required to install the toolkit's skills.

Install Node.js (https://nodejs.org) and re-run, or skip this step entirely:
Biologix talks to the NIMs over plain HTTP through its own adapters, so the
toolkit's skills are a convenience for agent workflows, not a dependency.
EOF
  exit 1
fi

if [[ "$LIST" == "1" ]]; then
  echo "Catalogue for ${REPO}:"
  exec npx skills add "$REPO" --list
fi

echo "Installing BioNeMo skills for '${AGENT}'"
failed=()
for skill in "${SKILLS[@]}"; do
  echo "  -> ${skill}"
  # One unavailable skill must not abort the rest; the catalogue changes faster
  # than this script does.
  if ! npx skills add "$REPO" --skill "$skill" --agent "$AGENT" --yes; then
    echo "warning: could not install '${skill}'; continuing" >&2
    failed+=("$skill")
  fi
done

echo
if [[ ${#failed[@]} -gt 0 ]]; then
  echo "warning: not installed: ${failed[*]}. Run with --list to see current names." >&2
fi

cat <<'EOF'
Next steps
----------
1. Set NVIDIA_API_KEY in .env for hosted NIM access (build.nvidia.com), or
   point the BIOLOGIX_*_BASE_URL variables at self-hosted containers.
   NGC_API_KEY is only needed to `docker login nvcr.io` and pull containers.

2. Check what can actually run:
     python -m biologix.mcp_server --check

3. Register the Biologix MCP server with your agent, e.g. for Claude Code:
     claude mcp add biologix -- python -m biologix.mcp_server

   The server's tools enforce the discipline the raw NIM skills cannot know
   about: a polymer specification is not a molecule, a generated molecule is
   not a repeat unit until it passes polymerisation rules, and every number
   carries an evidence tier. See docs/NVIDIA_SETUP.md.
EOF
