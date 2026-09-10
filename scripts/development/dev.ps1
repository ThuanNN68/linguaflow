# LinguaFlow local development launcher.
# Translation Agent and Assistant Agent are hosted by the FastAPI backend.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (
  Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
)
$backendPort = 8000
$frontendPort = 3000
$postgresPort = 5432
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logDirectory = Join-Path $projectRoot "logs\dev"

function Test-PortOpen([int]$Port) {
  return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Stop-PortListener([int]$Port, [string]$Name) {
  $processIds = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
  foreach ($processId in $processIds) {
    Write-Host "Stopping $Name on port $Port (PID $processId)..."
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
  }
}

function Wait-ForHttp([string]$Url, [int]$Seconds) {
  for ($attempt = 0; $attempt -lt $Seconds; $attempt++) {
    try {
      Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $Url | Out-Null
      return $true
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  return $false
}

Set-Location $projectRoot
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
if (!(Test-Path $python)) {
  throw "Python environment not found: $python"
}
if (!(Test-Path (Join-Path $projectRoot "frontend\node_modules"))) {
  throw "Frontend dependencies are missing. Run: cd frontend; npm install"
}

# Start PostgreSQL only when it is not already listening. Docker Desktop must
# have WSL integration enabled for the configured Ubuntu distribution.
if (!(Test-PortOpen $postgresPort)) {
  Write-Host "Starting local PostgreSQL..."
  $wslProjectRoot = (& wsl.exe wslpath -a ($projectRoot -replace '\\', '/')).Trim()
  & wsl.exe -e bash -lc "cd '$wslProjectRoot' && docker compose up -d postgres"
  if ($LASTEXITCODE -ne 0) { throw "Could not start PostgreSQL through WSL/Docker Desktop." }
  for ($attempt = 0; $attempt -lt 30 -and !(Test-PortOpen $postgresPort); $attempt++) { Start-Sleep -Seconds 1 }
  if (!(Test-PortOpen $postgresPort)) { throw "PostgreSQL did not become available on port $postgresPort." }
}

Stop-PortListener $backendPort "backend"
Stop-PortListener $frontendPort "frontend"

Write-Host "Applying database migrations..."
& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }

Write-Host "Starting backend (including Translation and Assistant Agents)..."
$backend = Start-Process -FilePath $python `
  -ArgumentList @("-B", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "$backendPort") `
  -WorkingDirectory $projectRoot -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $logDirectory "backend.out.log") `
  -RedirectStandardError (Join-Path $logDirectory "backend.err.log") -PassThru

Write-Host "Starting frontend..."
$frontend = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev", "--", "--port", "$frontendPort") `
  -WorkingDirectory (Join-Path $projectRoot "frontend") -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $logDirectory "frontend.out.log") `
  -RedirectStandardError (Join-Path $logDirectory "frontend.err.log") -PassThru

$backendReady = Wait-ForHttp "http://127.0.0.1:$backendPort/health" 180
$frontendReady = Wait-ForHttp "http://127.0.0.1:$frontendPort" 60

if (!$backendReady -or !$frontendReady) {
  throw "A service did not start. Check logs/dev/backend.err.log and logs/dev/frontend.err.log."
}

Write-Host ""
Write-Host "LinguaFlow is ready"
Write-Host "  Frontend: http://localhost:$frontendPort"
Write-Host "  Backend : http://localhost:$backendPort/docs"
Write-Host "  Process IDs: backend=$($backend.Id), frontend=$($frontend.Id)"
