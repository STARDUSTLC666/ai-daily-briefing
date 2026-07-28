param(
  [string]$Date = "today",
  [int]$Port = 8765,
  [string]$Quality = "1080p",
  [int]$MaxItems = 8,
  [switch]$NoPrepare
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$Repo = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Repo "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Resolve-RunDate([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value) -or $Value -eq "today") {
    return (Get-Date -Format "yyyy-MM-dd")
  }
  return $Value
}

function Test-PortOpen([int]$LocalPort) {
  $conn = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  return $null -ne $conn
}

function Wait-Port([int]$LocalPort, [int]$TimeoutSeconds = 20) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-PortOpen $LocalPort) {
      return $true
    }
    Start-Sleep -Milliseconds 350
  }
  return $false
}

function Find-Edge {
  $candidates = @(
    (Get-Command msedge.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
  )
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
      return $candidate
    }
  }
  return ""
}

Set-Location $Repo

$RunDate = Resolve-RunDate $Date
$RunDir = Join-Path $Repo ("runs\" + $RunDate)
$ReviewDir = Join-Path $RunDir "review"
$StatePath = Join-Path $ReviewDir "state.json"
$PidPath = Join-Path $ReviewDir "review-server.pid"
$ServerLog = Join-Path $LogDir ("review-app-server-" + $RunDate + ".log")
$ServerErr = Join-Path $LogDir ("review-app-server-" + $RunDate + ".err.log")
$Url = "http://127.0.0.1:$Port/"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  throw "Python launcher py not found."
}

if (-not $NoPrepare -and -not (Test-Path -LiteralPath $StatePath)) {
  Write-Host "Review package missing. Preparing local review package: $RunDate"
  & py -3 -m briefing prepare-review --date $RunDate --target bilibili --quality $Quality --max-items $MaxItems --workers 8
  if ($LASTEXITCODE -ne 0) {
    throw "prepare-review failed with exit code $LASTEXITCODE"
  }
}

if (-not (Test-Path -LiteralPath $RunDir)) {
  throw "run directory not found: $RunDir"
}

New-Item -ItemType Directory -Force -Path $ReviewDir | Out-Null

if (-not (Test-PortOpen $Port)) {
  Write-Host "Starting local review server: $Url"
  $args = @("-3", "-m", "briefing", "review", "--run-dir", $RunDir, "--port", [string]$Port)
  $proc = Start-Process -FilePath "py" `
    -ArgumentList $args `
    -WorkingDirectory $Repo `
    -WindowStyle Hidden `
    -RedirectStandardOutput $ServerLog `
    -RedirectStandardError $ServerErr `
    -PassThru
  Set-Content -LiteralPath $PidPath -Value ([string]$proc.Id) -Encoding ASCII
  if (-not (Wait-Port $Port 25)) {
    throw "Review server startup timed out. Logs: $ServerLog / $ServerErr"
  }
} else {
  Write-Host "Local review server already running: $Url"
}

$edge = Find-Edge
if ($edge) {
  Start-Process -FilePath $edge -ArgumentList @("--app=$Url", "--new-window")
} else {
  Start-Process $Url
}

Write-Host ""
Write-Host "AI briefing review app opened."
Write-Host "URL: $Url"
Write-Host "Run dir: $RunDir"
Write-Host "Stop server: powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\stop_review_app.ps1`" -Date $RunDate"
