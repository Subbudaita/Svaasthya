<#
.SYNOPSIS
  Windows equivalent of the Makefile. Same task names.

.EXAMPLE
  .\dev.ps1 install
  .\dev.ps1 db
  .\dev.ps1 migrate
  .\dev.ps1 indb
  .\dev.ps1 dev-user -Email you@example.com
  .\dev.ps1 telegram
  .\dev.ps1 ask -UserId <id> -Q "why am I so tired today?"

  If Windows says "running scripts is disabled on this system", run once:
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
param(
    [Parameter(Position = 0)][string]$Task = "help",
    [string]$Email,
    [string]$UserId,
    [string]$Q
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$IndbRepo = "https://github.com/lindsayjaacks/indian-nutrient-databank-indb-"
$IndbDir = "var/indb"

function Stop-With {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Red
    exit 1
}

function Invoke-Step {
    param([string]$Exe, [string[]]$Argv)
    Write-Host "> $Exe $($Argv -join ' ')" -ForegroundColor DarkGray
    & $Exe @Argv
    if ($LASTEXITCODE -ne 0) { Stop-With "'$Exe' failed with exit code $LASTEXITCODE (see the output above)." }
}

function Assert-Command {
    param([string]$Name, [string]$Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Stop-With "'$Name' was not found. $Hint Then close and reopen PowerShell."
    }
}

function Assert-EnvFile {
    if (-not (Test-Path ".env")) {
        Stop-With "No .env file yet. Run:  Copy-Item .env.example .env  and fill it in (docs/SETUP.md)."
    }
}

switch ($Task) {
    "install" {
        Assert-Command "uv" "Install it: powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`""
        Assert-Command "npm" "Install Node 20+ from https://nodejs.org"
        Invoke-Step "uv" @("sync", "--group", "data")
        Push-Location "apps/web"
        try { Invoke-Step "npm" @("ci") } finally { Pop-Location }
    }
    "db" {
        Assert-Command "docker" "Install Docker Desktop and start it."
        Assert-EnvFile
        Invoke-Step "docker" @("compose", "up", "-d", "--wait", "db")
    }
    "migrate" {
        Assert-EnvFile
        Push-Location "apps/api"
        try { Invoke-Step "uv" @("run", "--env-file", "../../.env", "alembic", "upgrade", "head") }
        finally { Pop-Location }
    }
    "indb" {
        if (-not (Test-Path $IndbDir)) {
            Invoke-Step "git" @("clone", "--depth", "1", $IndbRepo, $IndbDir)
        }
        Invoke-Step "uv" @("run", "--group", "data", "python", "scripts/import_indb.py", "$IndbDir/INDB.xlsx")
    }
    "dev-user" {
        if (-not $Email) { Stop-With "Usage: .\dev.ps1 dev-user -Email you@example.com" }
        Assert-EnvFile
        Invoke-Step "uv" @("run", "--env-file", ".env", "python", "-m", "swasthya_api.devtools",
                           "create-user", "--email", $Email)
    }
    "ask" {
        if (-not $UserId -or -not $Q) { Stop-With 'Usage: .\dev.ps1 ask -UserId <id> -Q "your question"' }
        Assert-EnvFile
        Invoke-Step "uv" @("run", "--env-file", ".env", "python", "-m", "swasthya_api.devtools",
                           "ask", "--user-id", $UserId, $Q)
    }
    "api" {
        Assert-EnvFile
        Invoke-Step "uv" @("run", "--env-file", ".env", "uvicorn", "--factory",
                           "swasthya_api.main:create_app", "--reload",
                           "--loop", "swasthya_api.aio:new_event_loop")
    }
    "telegram" {
        Assert-EnvFile
        Invoke-Step "uv" @("run", "--env-file", ".env", "python", "-m", "swasthya_api.worker")
    }
    "web" {
        Push-Location "apps/web"
        try { Invoke-Step "npm" @("run", "dev") } finally { Pop-Location }
    }
    "test" { Invoke-Step "uv" @("run", "pytest") }
    "eval-vision" {
        Assert-EnvFile
        Invoke-Step "uv" @("run", "--env-file", ".env", "python", "evals/vision/run.py")
    }
    default {
        Write-Host @"
Usage: .\dev.ps1 <task>

  install     Python + web dependencies
  db          start the local Postgres (Docker)
  migrate     create/upgrade the database schema
  indb        build the Indian food table
  dev-user    create a local user + Telegram link code   (-Email you@example.com)
  telegram    run the bot, rituals and nudges (leave it running)
  ask         ask the coach from the terminal            (-UserId <id> -Q "...")
  api         run the HTTP API
  web         run the web shell
  test        run the test suite
  eval-vision score plate recognition on your photos
"@
    }
}
