<#
.SYNOPSIS
    One-command startup for The Lenny Growth Assistant (Windows).

.DESCRIPTION
    Checks prerequisites, installs dependencies, applies migrations, and starts
    the API and web servers. Safe to re-run — every step is idempotent.

    This is the path verified on the development machine. docker-compose.yml
    exists for evaluators who have Docker, but was not runnable here (see the
    note at the top of that file).

.PARAMETER SkipInstall
    Skip dependency installation. Use on repeat runs for a faster start.

.PARAMETER Ingest
    Run ingestion before starting. Required on first run, or the corpus is
    empty and every question is refused.

.EXAMPLE
    ./scripts/start.ps1 -Ingest
#>
[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$Ingest
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'

function Write-Step($message) { Write-Host "`n=> $message" -ForegroundColor Cyan }
function Write-Ok($message) { Write-Host "   OK  $message" -ForegroundColor Green }
function Write-Warn($message) { Write-Host "   !   $message" -ForegroundColor Yellow }
function Write-Err($message) { Write-Host "   X   $message" -ForegroundColor Red }

# --------------------------------------------------------------------------
Write-Step 'Checking prerequisites'

foreach ($tool in @('node', 'npm')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Err "$tool not found. Install Node.js 20+ from https://nodejs.org"
        exit 1
    }
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Err 'uv not found. Install it: https://docs.astral.sh/uv/getting-started/installation/'
    exit 1
}
Write-Ok 'node, npm, uv'

$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root '.env.example') $envFile
    Write-Warn 'Created .env from .env.example — set DATABASE_URL before continuing.'
    Write-Warn "Edit: $envFile"
    exit 1
}
if ((Get-Content $envFile -Raw) -match 'DATABASE_URL=postgresql://postgres:password@localhost') {
    Write-Err 'DATABASE_URL is still the placeholder. Set a real PostgreSQL connection string in .env.'
    Write-Err 'A free Supabase project works: https://supabase.com  (Project Settings > Database > URI)'
    exit 1
}
Write-Ok '.env present with a DATABASE_URL'

# --------------------------------------------------------------------------
Write-Step 'Checking Ollama'

$ollamaUp = $false
try {
    $tags = Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -TimeoutSec 5
    $ollamaUp = $true
    $models = ($tags.models | ForEach-Object { $_.name }) -join ', '
    Write-Ok "running — models: $models"

    foreach ($needed in @('llama3.2', 'nomic-embed-text')) {
        if ($models -notmatch [regex]::Escape($needed)) {
            Write-Warn "$needed not pulled. Fetching..."
            ollama pull $needed
        }
    }
} catch {
    Write-Warn 'Ollama is not reachable at http://localhost:11434'
    Write-Warn 'Start it with `ollama serve`, or set LLM_PROVIDER=azure in .env to use a cloud model.'
    Write-Warn 'Continuing — /health will report the provider as unavailable.'
}

# --------------------------------------------------------------------------
if (-not $SkipInstall) {
    Write-Step 'Installing backend dependencies'
    Push-Location $backend
    if (-not (Test-Path $python)) { uv venv --python 3.12 }
    uv pip install -e '.[dev]' --quiet
    Pop-Location
    Write-Ok 'backend'

    Write-Step 'Installing frontend dependencies'
    Push-Location $frontend
    npm install --no-audit --no-fund --silent
    Pop-Location
    Write-Ok 'frontend'
}

# --------------------------------------------------------------------------
Write-Step 'Applying database migrations'
Push-Location $backend
& $python -m app.db.migrate
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Err 'Migrations failed. Check DATABASE_URL, and that the project is awake if using Supabase.'
    exit 1
}
Pop-Location
Write-Ok 'schema up to date'

# --------------------------------------------------------------------------
if ($Ingest) {
    if (-not $ollamaUp) {
        Write-Err 'Ingestion needs an embedding model. Start Ollama first, or set EMBED_PROVIDER=azure.'
        exit 1
    }
    Write-Step 'Ingesting transcripts (this takes 10-20 minutes on CPU)'
    Push-Location $backend
    & $python -m app.ingest.pipeline
    Pop-Location
    Write-Ok 'corpus built — see INGESTED.md'
}

# --------------------------------------------------------------------------
Write-Step 'Starting servers'

$apiJob = Start-Process -FilePath $python `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000' `
    -WorkingDirectory $backend -PassThru -NoNewWindow

Start-Sleep -Seconds 4

try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 20
    if ($health.status -eq 'ok') { Write-Ok 'API healthy on http://127.0.0.1:8000' }
    else {
        Write-Warn "API started but degraded. Components:"
        $health.components.PSObject.Properties | ForEach-Object {
            $state = if ($_.Value.ok) { 'ok' } else { 'FAILING' }
            Write-Host ("       {0,-22} {1}" -f $_.Name, $state)
        }
    }
} catch {
    Write-Err 'API did not come up. Check the output above.'
}

$webJob = Start-Process -FilePath 'npm' -ArgumentList 'run', 'dev' `
    -WorkingDirectory $frontend -PassThru -NoNewWindow

Write-Host ''
Write-Host '  The Lenny Growth Assistant is running.' -ForegroundColor Green
Write-Host '    Web    http://localhost:5173'
Write-Host '    API    http://127.0.0.1:8000'
Write-Host '    Docs   http://127.0.0.1:8000/docs'
Write-Host '    Health http://127.0.0.1:8000/health'
Write-Host ''
Write-Host '  Press Ctrl+C to stop.' -ForegroundColor DarkGray

try {
    Wait-Process -Id $apiJob.Id
} finally {
    foreach ($proc in @($apiJob, $webJob)) {
        if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    }
}
