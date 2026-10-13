# SentinelChat dev data reset (Windows). Deletes the LOCAL DEV DATA so you start from a clean slate:
# Postgres (users, chats, cases, tickets), Qdrant (chat memories, Work-tab runs) and the local
# upload / run / log folders. It never touches .env, other Docker projects, or `docker volume prune`.
#
# Usage:
#   powershell -File scripts\reset-dev.ps1                    # wipe this project's Docker volumes + local files,
#                                                             # then bring up an empty, migrated database
#   powershell -File scripts\reset-dev.ps1 -KeepStack         # keep the containers running; empty Postgres and
#                                                             # Qdrant in place (keeps the IPC standards index)
#   powershell -File scripts\reset-dev.ps1 -KeepStack -IncludeStandards   # ...and drop the standards index too
#   powershell -File scripts\reset-dev.ps1 -Force            # no confirmation prompt
#   powershell -File scripts\reset-dev.ps1 -DryRun           # say what would happen, change nothing
#   powershell -File scripts\reset-dev.ps1 -NoRebuild        # do not bring Postgres/Qdrant up and migrate afterwards
#
# After a reset the first user you register becomes Admin. Also clear the browser's localStorage
# (DevTools > Application > Local Storage) or you will see ghost conversations.

param(
    [switch]$KeepStack,
    [switch]$IncludeStandards,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$NoRebuild
)

# Same reasoning as setup-dev.ps1: a native command's harmless stderr output must not terminate the
# script, so every native call below checks $LASTEXITCODE itself.
$ErrorActionPreference = "Continue"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..") -ErrorAction Stop).Path
Set-Location $RootDir

$ComposeFile = "infra/development/docker-compose.yml"
# The IPC standards index Agent 2's precedent search reads (the current name, and the older one the
# legacy indexer and agent2_config.yaml still use). It costs embeddings to rebuild, so a KeepStack
# reset keeps it unless asked.
$StandardsCollections = @("pcb_standards_precedents", "ipc_defect_precedents")

function Get-EnvValue([string]$Name, [string]$Default) {
    if (Test-Path ".env") {
        $line = Select-String -Path ".env" -Pattern "^$Name=(.*)$" -ErrorAction SilentlyContinue | Select-Object -Last 1
        if ($line) {
            $value = $line.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'")
            if ($value -ne "") { return $value }
        }
    }
    return $Default
}

function Step([string]$Text) {
    if ($DryRun) { Write-Host "[dry-run] $Text" } else { Write-Host "==> $Text" }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: docker not found. Install Docker Desktop for Windows first."
    exit 1
}
docker info > $null 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker daemon is not running. Start Docker Desktop and retry."
    exit 1
}

$compose = @("compose", "-f", $ComposeFile)
if (Test-Path ".env") { $compose += @("--env-file", ".env") }

$qdrantUrl = (Get-EnvValue "QDRANT_URL" "http://localhost:6333").TrimEnd("/")
$dbUrl = Get-EnvValue "DATABASE_URL" "postgresql+asyncpg://sentinelchat:sentinelchat@localhost:5433/sentinelchat"
if ($dbUrl -match "^postgresql(\+\w+)?://([^:]+):([^@]*)@[^/]+/([^?]+)") {
    $dbUser = $Matches[2]
    $dbName = $Matches[4]
} else {
    $dbUser = "sentinelchat"
    $dbName = "sentinelchat"
}

# The local folders the backend writes to (settings defaults, overridable in .env).
$dataDirs = @()
foreach ($pair in @(
    @("CHAT_UPLOAD_DIR", "data/uploads"),
    @("ORCHESTRATOR_DATA_DIR", "data/orchestrator"),
    @("LOG_DIR", "data/logs")
)) {
    $relative = Get-EnvValue $pair[0] $pair[1]
    $full = [System.IO.Path]::GetFullPath((Join-Path $RootDir $relative))
    # Never delete anything outside the repo, whatever .env says.
    if ($full.StartsWith($RootDir + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        $dataDirs += $full
    } else {
        Write-Host "    Skipping $($pair[0])=$relative - it is outside the repo."
    }
}

# What is in the standards index, so the prompt can say what a full wipe costs.
$standardsPoints = 0
foreach ($collection in $StandardsCollections) {
    try {
        $info = Invoke-RestMethod -Uri "$qdrantUrl/collections/$collection" -TimeoutSec 3 -ErrorAction Stop
        $standardsPoints += [int]$info.result.points_count
    } catch {
        # Qdrant not running or no such collection - nothing to warn about.
    }
}

$backendRunning = $null -ne (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue)

Write-Host ""
Write-Host "This will DELETE the local dev data:"
if ($KeepStack) {
    Write-Host "  - Postgres '$dbName': every table (the schema is dropped and recreated)"
    if ($IncludeStandards) {
        Write-Host "  - Qdrant ($qdrantUrl): every collection, including the IPC standards index"
    } else {
        Write-Host "  - Qdrant ($qdrantUrl): every collection EXCEPT the IPC standards index"
    }
} else {
    Write-Host "  - this project's Docker volumes (sentinelchat_*): Postgres, Qdrant, LangFuse"
    if ($standardsPoints) {
        Write-Host "    Qdrant holds the IPC standards index ($standardsPoints points); it goes too and must be"
        Write-Host "    re-ingested (embeddings cost money). Use -KeepStack to keep it."
    }
}
foreach ($dir in $dataDirs) {
    if (Test-Path $dir) { Write-Host "  - $dir" }
}
Write-Host "It does NOT touch .env or any other Docker project."
if ($backendRunning) {
    Write-Host ""
    Write-Host "WARNING: something is listening on port 8000 (the backend?). Stop it first so it does not"
    Write-Host "         hold the database open or write new data mid-reset."
}
Write-Host ""

if ($DryRun) {
    Write-Host "[dry-run] nothing will be changed."
} elseif (-not $Force) {
    $answer = Read-Host "Type 'yes' to continue"
    if ($answer -ne "yes") {
        Write-Host "Aborted - nothing was changed."
        exit 0
    }
}

if ($KeepStack) {
    # --- in place: containers keep running ---
    Step "Postgres: dropping and recreating the public schema in '$dbName'"
    if (-not $DryRun) {
        docker @compose exec -T db psql -U $dbUser -d $dbName -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: could not reset Postgres. Is the 'db' container running? (docker compose ... up -d db)"
            exit $LASTEXITCODE
        }
    }

    Step "Qdrant: deleting collections"
    try {
        $names = @((Invoke-RestMethod -Uri "$qdrantUrl/collections" -TimeoutSec 5 -ErrorAction Stop).result.collections | ForEach-Object { $_.name })
        foreach ($name in $names) {
            if ($StandardsCollections -contains $name -and -not $IncludeStandards) {
                Write-Host "    keeping $name (the IPC standards index; -IncludeStandards drops it)"
                continue
            }
            Write-Host "    deleting $name"
            if (-not $DryRun) {
                Invoke-RestMethod -Method Delete -Uri "$qdrantUrl/collections/$name" -TimeoutSec 30 -ErrorAction Stop | Out-Null
            }
        }
    } catch {
        Write-Host "WARNING: could not reach Qdrant at $qdrantUrl ($($_.Exception.Message)); its data was not touched."
    }
} else {
    # --- full: remove the project's volumes ---
    Step "Docker: stopping the stack and removing this project's volumes"
    if (-not $DryRun) {
        docker @compose --profile full down -v --remove-orphans
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: docker compose down failed (exit $LASTEXITCODE). Nothing else was deleted."
            exit $LASTEXITCODE
        }
    }
}

foreach ($dir in $dataDirs) {
    if (Test-Path $dir) {
        Step "Deleting $dir"
        if (-not $DryRun) { Remove-Item -Recurse -Force $dir -ErrorAction Continue }
    }
}

if (-not $NoRebuild) {
    if (-not $KeepStack) {
        Step "Starting an empty Postgres + Qdrant (docker compose up -d --wait)"
        if (-not $DryRun) {
            docker @compose up -d --wait db qdrant
            if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: could not start Postgres + Qdrant."; exit $LASTEXITCODE }
        }
    }
    Step "Creating the schema (uv run alembic upgrade head)"
    if (-not $DryRun) {
        uv run alembic upgrade head
        if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: alembic upgrade head failed."; exit $LASTEXITCODE }
    }
}

Write-Host ""
if ($DryRun) {
    Write-Host "Dry run finished - nothing was changed."
} else {
    Write-Host "Done. Next:"
    Write-Host "  1. Clear the browser's localStorage for the app (DevTools > Application > Local Storage)."
    Write-Host "  2. Start the app (scripts or infra\development\scripts\windows\start-dev.ps1) and register - the"
    Write-Host "     first user becomes Admin."
    if ($NoRebuild) {
        Write-Host "  (You skipped the rebuild: run setup-dev.ps1, then 'uv run alembic upgrade head'.)"
    }
}
