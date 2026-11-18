param(
  [ValidateSet("auto", "generate", "publish", "check", "prepare-review", "morning-render")]
  [string]$Mode = "auto",
  [string]$Quality = "1080p",
  [string]$Tts = "edge",
  [string]$Profile = "",
  [int]$MaxItems = 0,
  [int]$MaxNewItems = 3,
  [int]$Tid = 0,
  [string]$ScheduleAt = "",
  [switch]$Publish,
  [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$Repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'python_runtime.ps1')
$PythonRuntime = Resolve-BriefingPython -Repo $Repo
$PythonPrefix = @($PythonRuntime.Prefix)
$CredentialLoader = Join-Path $PSScriptRoot "load_bilibili_credentials.ps1"
if (Test-Path -LiteralPath $CredentialLoader) {
  . $CredentialLoader
}
$LogDir = Join-Path $Repo "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$RunDate = Get-Date -Format "yyyy-MM-dd"
$RunDir = Join-Path $Repo ("runs\" + $RunDate)
$Log = Join-Path $LogDir ("daily-run-" + $RunDate + ".log")
$SummaryPath = Join-Path $LogDir ("daily-summary-" + $RunDate + "-" + $Mode + ".json")

function Write-Log([string]$Message) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  $line | Tee-Object -FilePath $Log -Append
}

function Save-Summary($Summary) {
  $Summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $SummaryPath -Encoding UTF8
}

function Send-AutomationAlert([string]$Message) {
  $AlertPath = Join-Path $LogDir ("attention-required-" + $RunDate + ".json")
  $Alert = [ordered]@{
    status = "attention_required"
    run_date = $RunDate
    created_at = (Get-Date).ToString("s")
    message = $Message
    summary = $SummaryPath
    log = $Log
  }
  $Body = $Alert | ConvertTo-Json -Depth 5
  $Body | Set-Content -LiteralPath $AlertPath -Encoding UTF8
  if ($env:BRIEFING_ALERT_WEBHOOK) {
    try {
      Invoke-RestMethod -Method Post -Uri $env:BRIEFING_ALERT_WEBHOOK -ContentType "application/json; charset=utf-8" -Body $Body -TimeoutSec 15 | Out-Null
    } catch {
      Write-Log ("Alert webhook failed: " + $_.Exception.Message)
    }
  }
}

function Run-Briefing([string]$Label, [string]$OutputPath, [string[]]$SubArgs) {
  Write-Log $Label
  $previousPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $output = & $PythonRuntime.Executable @PythonPrefix -m briefing @SubArgs 2>&1
    $NativeExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  $stdoutLines = New-Object System.Collections.Generic.List[string]
  foreach ($item in @($output)) {
    if ($item -is [System.Management.Automation.ErrorRecord]) {
      Write-Log ("[stderr] " + $item.ToString())
    } elseif ($null -ne $item) {
      $line = [string]$item
      $stdoutLines.Add($line)
      Write-Log $line
    }
  }
  Set-Content -LiteralPath $OutputPath -Value ($stdoutLines -join "`n") -Encoding UTF8
  if ($NativeExitCode -ne 0) {
    throw "$Label failed with exit code $NativeExitCode"
  }
}

Set-Location $Repo
$GitSha = (& git rev-parse HEAD 2>$null | Out-String).Trim()
$GitStatus = (& git status --porcelain=v1 2>$null | Out-String)
$GitDirty = -not [string]::IsNullOrWhiteSpace($GitStatus)

$Summary = [ordered]@{
  run_date = $RunDate
  run_dir = $RunDir
  generated_at = (Get-Date).ToString("s")
  mode = $Mode
  quality = $Quality
  tts_backend = $Tts
  generated = $false
  preflight_ok = $false
  ready_to_upload = $false
  published = $false
  git_sha = $GitSha
  git_dirty = $GitDirty
}

try {
  if ((@("auto", "generate", "morning-render") -contains $Mode) -and $GitDirty -and -not $AllowDirty) {
    throw "Refusing unattended generation from a dirty Git worktree. Commit verified changes first, or pass -AllowDirty for diagnostics."
  }

  if ($Mode -eq "prepare-review") {
    $ArgsList = @("prepare", "--mode", "review", "--date", "today", "--quality", $Quality)
    if ($MaxItems -gt 0) { $ArgsList += @("--max-items", [string]$MaxItems) }
    Run-Briefing "Preparing review workspace." (Join-Path $LogDir ("daily-review-" + $RunDate + ".json")) $ArgsList
    $Summary.generated = $true
    $Summary.completed_at = (Get-Date).ToString("s")
    Save-Summary $Summary
    return
  }

  if (@("auto", "generate") -contains $Mode) {
    $RunArgs = @("run", "--date", "today", "--quality", $Quality, "--tts", $Tts)
    if ($Profile) { $RunArgs += @("--profile", $Profile) }
    if ($MaxItems -gt 0) { $RunArgs += @("--max-items", [string]$MaxItems) }
    Run-Briefing "Running unattended briefing pipeline." (Join-Path $LogDir ("daily-pipeline-" + $RunDate + ".json")) $RunArgs
    $Summary.generated = $true
  } elseif ($Mode -eq "morning-render") {
    $PrepArgs = @("prepare", "--mode", "review", "--append-morning", "--date", "today", "--max-new-items", [string]$MaxNewItems)
    if ($MaxItems -gt 0) { $PrepArgs += @("--max-items", [string]$MaxItems) }
    Run-Briefing "Merging morning stories." (Join-Path $LogDir ("daily-morning-prep-" + $RunDate + ".json")) $PrepArgs

    $RenArgs = @("render", "--run-dir", $RunDir, "--quality", $Quality, "--tts", $Tts)
    if ($Profile) { $RenArgs += @("--profile", $Profile) }
    Run-Briefing "Rendering morning edition." (Join-Path $LogDir ("daily-morning-render-" + $RunDate + ".json")) $RenArgs
    $Summary.generated = $true
  } elseif (-not (Test-Path -LiteralPath $RunDir)) {
    throw "Run directory not found for $Mode mode: $RunDir"
  }

  if ((@("publish", "check") -contains $Mode) -and $env:BILI_ACCESS_TOKEN) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
      $AuthOut = & $PythonRuntime.Executable @PythonPrefix -m briefing publish --check-auth 2>&1 | Out-String
      $AuthExit = $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $previousPreference
    }
    $Summary.token_probe_ok = ($AuthExit -eq 0)
    if ($AuthExit -eq 1) {
      Send-AutomationAlert "Bilibili access token probe failed: $AuthOut"
    }
  }

  # Single preflight pass (includes full 6-gate verify_run_dir internally)
  $PubArgs = @("publish", "--run-dir", $RunDir)
  if ($Tid -gt 0) { $PubArgs += @("--tid", [string]$Tid) }
  if ($ScheduleAt) { $PubArgs += @("--schedule-at", $ScheduleAt) }
  $PreflightJson = Join-Path $RunDir "bilibili-preflight-result.json"
  Run-Briefing "Running Bilibili preflight & verification." $PreflightJson $PubArgs
  $Preflight = Get-Content -LiteralPath $PreflightJson -Raw -Encoding UTF8 | ConvertFrom-Json
  $Summary.preflight_ok = [bool]$Preflight.ok
  $Summary.ready_to_upload = [bool]$Preflight.ready_to_upload

  try {
    Run-Briefing "Exporting 9:16 vertical video and WeChat article." (Join-Path $RunDir "repurpose-result.json") @("repurpose", "--run-dir", $RunDir)
    $Summary.repurposed = $true
  } catch {
    $Summary.repurposed = $false
    Write-Log ("Repurpose step failed (non-fatal): " + $_.Exception.Message)
  }

  if ($Publish -and $env:BRIEFING_AUTO_PUBLISH -eq "1") {
    if (-not $Summary.ready_to_upload) {
      throw "Publish requested, but preflight is not ready_to_upload"
    }
    Run-Briefing "Uploading archive to Bilibili." (Join-Path $RunDir "bilibili-publish-result.json") @($PubArgs + "--execute")
    $Summary.published = $true
  }

  if ($env:BRIEFING_PRUNE_KEEP_DAYS -match '^\d+$' -and [int]$env:BRIEFING_PRUNE_KEEP_DAYS -gt 0) {
    try {
      Run-Briefing "Pruning old heavy media." (Join-Path $LogDir ("prune-runs-" + $RunDate + ".json")) @("repurpose", "--prune-days", $env:BRIEFING_PRUNE_KEEP_DAYS, "--execute")
    } catch {
      Write-Log ("Prune step failed (non-fatal): " + $_.Exception.Message)
    }
  }

  $Summary.completed_at = (Get-Date).ToString("s")
  Save-Summary $Summary
  Remove-Item -LiteralPath (Join-Path $LogDir ("attention-required-" + $RunDate + ".json")) -ErrorAction SilentlyContinue
} catch {
  $Summary.error = [string]$_.Exception.Message
  $Summary.failed_at = (Get-Date).ToString("s")
  Save-Summary $Summary
  Send-AutomationAlert $Summary.error
  throw
}
