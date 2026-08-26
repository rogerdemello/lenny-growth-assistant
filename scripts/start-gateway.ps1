<#
.SYNOPSIS
    Start the Anthropic-Messages gateway in front of Azure OpenAI.

.DESCRIPTION
    Lets the Claude Agent SDK runtime run without an Anthropic API key.
    See gateway/README.md for what it solves and why each piece is needed.

.EXAMPLE
    ./scripts/start-gateway.ps1
#>
[CmdletBinding()]
param([int]$Port = 4000)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$gateway = Join-Path $root 'gateway'
$python = Join-Path $root 'backend\.venv\Scripts\python.exe'
$litellm = Join-Path $root 'backend\.venv\Scripts\litellm.exe'

if (-not (Test-Path $litellm)) {
    Write-Host "litellm not installed. Run:" -ForegroundColor Red
    Write-Host '  cd backend; uv pip install -e ".[agent-sdk,gateway]"'
    exit 1
}

# The gateway reads Azure credentials from the environment; the config file
# holds no secrets. Load them from .env here.
$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) { Write-Host ".env not found." -ForegroundColor Red; exit 1 }

foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*#') { continue }
    if ($line -match '^(AZURE_OPENAI_[A-Z_]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), 'Process')
    }
}

foreach ($required in @('AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_API_VERSION')) {
    if (-not [Environment]::GetEnvironmentVariable($required, 'Process')) {
        Write-Host "$required is not set in .env" -ForegroundColor Red
        exit 1
    }
}

# clamp.py is imported by name from the config, so its directory must be importable.
$env:PYTHONPATH = $gateway

Write-Host ''
Write-Host '  Anthropic-Messages gateway -> Azure OpenAI' -ForegroundColor Cyan
Write-Host "    listening on  http://127.0.0.1:$Port"
Write-Host '    model alias   claude-azure-gpt4o'
Write-Host ''
Write-Host '  Then set in .env and restart the API:' -ForegroundColor DarkGray
Write-Host '    AGENT_RUNTIME=claude_sdk'
Write-Host "    ANTHROPIC_BASE_URL=http://127.0.0.1:$Port"
Write-Host '    ANTHROPIC_AUTH_TOKEN=sk-gateway-local-only'
Write-Host '    ANTHROPIC_MODEL=claude-azure-gpt4o'
Write-Host ''

& $litellm --config (Join-Path $gateway 'litellm_config.yaml') --port $Port
