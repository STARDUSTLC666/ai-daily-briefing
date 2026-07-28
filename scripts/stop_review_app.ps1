param(
  [string]$Date = "today",
  [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$Repo = Split-Path -Parent $PSScriptRoot

function Resolve-RunDate([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value) -or $Value -eq "today") {
    return (Get-Date -Format "yyyy-MM-dd")
  }
  return $Value
}

$RunDate = Resolve-RunDate $Date
$RunDir = Join-Path $Repo ("runs\" + $RunDate)
$PidPath = Join-Path $RunDir "review\review-server.pid"

$pids = @()
if (Test-Path -LiteralPath $PidPath) {
  $raw = (Get-Content -LiteralPath $PidPath -Raw).Trim()
  if ($raw -match '^\d+$') {
    $pids += [int]$raw
  }
}

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
  if ($listener.OwningProcess -and ($pids -notcontains [int]$listener.OwningProcess)) {
    $pids += [int]$listener.OwningProcess
  }
}

if (-not $pids) {
  Write-Host "No running review server found."
  return
}

foreach ($procId in ($pids | Select-Object -Unique)) {
  try {
    $proc = Get-Process -Id $procId -ErrorAction Stop
    Stop-Process -Id $procId -Force
    Write-Host "Stopped review server process: $procId ($($proc.ProcessName))"
  } catch {
    Write-Host "Process missing or cannot be stopped: $procId"
  }
}

if (Test-Path -LiteralPath $PidPath) {
  Remove-Item -LiteralPath $PidPath -Force
}
