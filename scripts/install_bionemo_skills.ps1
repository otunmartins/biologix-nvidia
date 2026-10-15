<#
.SYNOPSIS
    Install NVIDIA BioNeMo Agent Toolkit skills for use alongside Biologix.

.DESCRIPTION
    The BioNeMo Agent Toolkit publishes agent skills that call BioNeMo NIMs
    directly. Biologix's own MCP server (biologix-mcp) exposes the polymer
    pipeline. The two are complementary: install these skills for direct model
    access, and register the MCP server for the pipeline that enforces the
    repeat-unit, oligomer and evidence-tier discipline.

    This script only installs skills. It does not start any NIM container and
    does not need a GPU.

.PARAMETER Agent
    Target agent: claude-code (default) or codex.

.PARAMETER Skills
    Skills to install. Defaults to the five relevant to this project.

.PARAMETER List
    Print the toolkit's full catalogue and exit without installing.

.EXAMPLE
    .\scripts\install_bionemo_skills.ps1 -List

.EXAMPLE
    .\scripts\install_bionemo_skills.ps1 -Agent claude-code
#>
[CmdletBinding()]
param(
    [ValidateSet('claude-code', 'codex')]
    [string]$Agent = 'claude-code',

    [string[]]$Skills = @('boltz2-nim', 'diffdock-nim', 'molmim-nim', 'genmol-nim', 'nvmolkit'),

    [switch]$List
)

$ErrorActionPreference = 'Stop'
$Repo = 'NVIDIA-BioNeMo/bionemo-agent-toolkit'

if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
    Write-Error @'
npx was not found on PATH. The skills CLI is distributed via npm, so Node.js is
required to install the toolkit's skills.

Install Node.js (https://nodejs.org) and re-run, or skip this step entirely:
Biologix talks to the NIMs over plain HTTP through its own adapters, so the
toolkit's skills are a convenience for agent workflows, not a dependency.
'@
}

if ($List) {
    Write-Host "Catalogue for $Repo :" -ForegroundColor Cyan
    & npx skills add $Repo --list
    exit $LASTEXITCODE
}

Write-Host "Installing BioNeMo skills for '$Agent'" -ForegroundColor Cyan
$failed = @()
foreach ($skill in $Skills) {
    Write-Host "  -> $skill"
    & npx skills add $Repo --skill $skill --agent $Agent --yes
    if ($LASTEXITCODE -ne 0) {
        # One unavailable skill must not abort the rest; the catalogue changes
        # faster than this script does.
        Write-Warning "could not install '$skill' (exit $LASTEXITCODE); continuing"
        $failed += $skill
    }
}

Write-Host ''
if ($failed.Count -gt 0) {
    Write-Warning "not installed: $($failed -join ', '). Run with -List to see current names."
}

Write-Host @'
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
'@ -ForegroundColor Green
