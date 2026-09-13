param(
  [ValidateSet("auto", "full", "generate", "publish", "check", "prepare-review", "morning-render")]
  [string]$Mode = "auto",
  [string]$Quality = "1080p",
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
if (-not $env:BRIEFING_RENDER_ENGINE) {
  $env:BRIEFING_RENDER_ENGINE = "remotion-only"
  $env:BRIEFING_STRICT_REMOTION = "1"
}
if (-not $env:BRIEFING_REMOTION_TIMEOUT_SECONDS) {
  $env:BRIEFING_REMOTION_TIMEOUT_SECONDS = "7200"
}
if (-not $env:BRIEFING_TTS_BACKEND) {
  $env:BRIEFING_TTS_BACKEND = "edge"
}
if ($env:BRIEFING_TTS_BACKEND -eq "edge") {
  if (-not $env:BRIEFING_TTS_VOICE) {
    $env:BRIEFING_TTS_VOICE = "zh-CN-XiaoxiaoNeural"
  }
  if (-not $env:BRIEFING_TTS_RATE) {
    $env:BRIEFING_TTS_RATE = "+8%"
  }
  if (-not $env:BRIEFING_TTS_PITCH) {
    $env:BRIEFING_TTS_PITCH = "+0Hz"
  }
} elseif ($env:BRIEFING_TTS_BACKEND -eq "indextts2") {
  if (-not $env:BRIEFING_TTS_PROFILE) {
    throw "BRIEFING_TTS_PROFILE is required when BRIEFING_TTS_BACKEND=indextts2"
  }
  if (-not $env:BRIEFING_INDEXTTS2_ROOT) {
    throw "BRIEFING_INDEXTTS2_ROOT is required when BRIEFING_TTS_BACKEND=indextts2"
  }
  if (-not $env:BRIEFING_VOICE_PROFILE_DIR) {
    $env:BRIEFING_VOICE_PROFILE_DIR = Join-Path $env:BRIEFING_INDEXTTS2_ROOT "voice_profiles"
  }
}

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
      Write-Log "Automation alert webhook delivered."
    } catch {
      Write-Log ("Automation alert webhook failed: " + $_.Exception.Message)
    }
  }
}

function Send-AutomationNotice($Notice) {
  $Url = $env:BRIEFING_NOTIFY_WEBHOOK
  if (-not $Url) {
    $Url = $env:BRIEFING_ALERT_WEBHOOK
  }
  if (-not $Url) {
    return
  }
  try {
    $Body = $Notice | ConvertTo-Json -Depth 6
    Invoke-RestMethod -Method Post -Uri $Url -ContentType "application/json; charset=utf-8" -Body $Body -TimeoutSec 15 | Out-Null
    Write-Log "Automation success notice delivered."
  } catch {
    Write-Log ("Automation success notice failed: " + $_.Exception.Message)
  }
}

function Run-Logged([string]$Label, [string]$OutputPath, [string[]]$Args) {
  Write-Log $Label
  # stdout goes ONLY into $OutputPath: several of these files are parsed as JSON, so a
  # single stderr warning merged into them would break ConvertFrom-Json. Also avoid
  # Tee-Object for the output file: under Windows PowerShell 5.1 it writes UTF-16,
  # which the UTF-8 readers below reject. stderr still lands in the daily log for
  # diagnosis, and a native stderr line must not become a terminating error here.
  $previousPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $NativeArgs = @($Args)
    if ($NativeArgs.Count -and $NativeArgs[0] -eq '-3') { $NativeArgs = @($NativeArgs | Select-Object -Skip 1) }
    $output = & $PythonRuntime.Executable @PythonPrefix @NativeArgs 2>&1
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
Write-Log "Starting daily briefing. Repo=$Repo Mode=$Mode Quality=$Quality MaxItems=$MaxItems ScheduleAt=$ScheduleAt"

$GitSha = (& git rev-parse HEAD 2>$null | Out-String).Trim()
$GitStatus = (& git status --porcelain=v1 2>$null | Out-String)
$GitDirty = -not [string]::IsNullOrWhiteSpace($GitStatus)
$StatusBytes = [System.Text.Encoding]::UTF8.GetBytes($GitStatus)
$Hasher = [System.Security.Cryptography.SHA256]::Create()
try {
  $GitFingerprint = ([BitConverter]::ToString($Hasher.ComputeHash($StatusBytes))).Replace("-", "").ToLowerInvariant()
} finally {
  $Hasher.Dispose()
}
$Summary = [ordered]@{
  run_date = $RunDate
  run_dir = $RunDir
  generated_at = (Get-Date).ToString("s")
  mode = $Mode
  quality = $Quality
  max_items = $MaxItems
  max_new_items = $MaxNewItems
  render_engine = $env:BRIEFING_RENDER_ENGINE
  tts_backend = $env:BRIEFING_TTS_BACKEND
  tts_profile = $(if ($env:BRIEFING_TTS_BACKEND -eq "indextts2") { $env:BRIEFING_TTS_PROFILE } else { $null })
  tts_voice = $(if ($env:BRIEFING_TTS_BACKEND -eq "edge") { $env:BRIEFING_TTS_VOICE } else { $null })
  tts_rate = $(if ($env:BRIEFING_TTS_BACKEND -eq "edge") { $env:BRIEFING_TTS_RATE } else { $null })
  tts_pitch = $(if ($env:BRIEFING_TTS_BACKEND -eq "edge") { $env:BRIEFING_TTS_PITCH } else { $null })
  remotion_timeout_seconds = $env:BRIEFING_REMOTION_TIMEOUT_SECONDS
  tid = $(if ($Tid -gt 0) { $Tid } else { $null })
  schedule_at = $(if ($ScheduleAt) { $ScheduleAt } else { $null })
  generated = $false
  automated = (@("auto", "generate", "publish") -contains $Mode)
  verified = $false
  preflight_ok = $false
  ready_to_upload = $false
  dry_run_ok = $false
  published = $false
  publish_requested = [bool]$Publish
  publish_enabled_by_env = ($env:BRIEFING_AUTO_PUBLISH -eq "1")
  git_sha = $GitSha
  git_dirty = $GitDirty
  git_status_fingerprint = $GitFingerprint
}

try {
  if ((@("auto", "full", "generate", "morning-render") -contains $Mode) -and $GitDirty -and -not $AllowDirty) {
    throw "Refusing unattended generation from a dirty Git worktree. Commit verified changes first, or use -AllowDirty only for an explicit diagnostic run."
  }
  if ($Mode -eq "prepare-review") {
    $ReviewArgs = @("-3", "-m", "briefing", "prepare-review", "--date", "today", "--target", "bilibili", "--quality", $Quality, "--max-items", [string]$MaxItems, "--workers", "8")
    Run-Logged "Preparing midnight review package." (Join-Path $LogDir ("daily-review-" + $RunDate + ".txt")) $ReviewArgs
    $Summary.generated = $true
    $Summary.review_only = $true
    $Summary.completed_at = (Get-Date).ToString("s")
    Save-Summary $Summary
    Write-Log "Review package prepared. No final video is rendered in prepare-review mode."
    return
  } elseif (@("auto", "full", "generate") -contains $Mode) {
    $RunArgs = @("-3", "-m", "briefing", "run", "--date", "today", "--target", "bilibili", "--quality", $Quality, "--workers", "8")
    if ($MaxItems -gt 0) {
      $RunArgs += @("--max-items", [string]$MaxItems)
    }
    Run-Logged "Running automatic briefing pipeline without manual review." (Join-Path $LogDir ("daily-pipeline-" + $RunDate + ".txt")) $RunArgs
    $Summary.generated = $true
  } elseif ($Mode -eq "morning-render") {
    $RunArgs = @("-3", "-m", "briefing", "morning-render", "--date", "today", "--target", "bilibili", "--quality", $Quality, "--max-items", [string]$MaxItems, "--max-new-items", [string]$MaxNewItems, "--workers", "8")
    Run-Logged "Running 06:00 six-hour refresh and final render." (Join-Path $LogDir ("daily-morning-render-" + $RunDate + ".txt")) $RunArgs
    $Summary.generated = $true
  } elseif (@("publish", "check") -contains $Mode) {
    if (-not (Test-Path -LiteralPath $RunDir)) {
      throw "run package not found for check mode: $RunDir"
    }
    Write-Log "$Mode mode: using existing run package: $RunDir"
  } else {
    throw "unsupported automation mode: $Mode"
  }

  if ((@("publish", "check") -contains $Mode) -and $env:BILI_ACCESS_TOKEN) {
    # Live token probe: one cheap authenticated call. A dead token this close
    # to upload deserves an immediate alert instead of a silent preflight fail.
    Write-Log "Probing Bilibili open-platform token."
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
      $AuthOutput = & $PythonRuntime.Executable @PythonPrefix -m briefing bilibili-check-auth 2>&1 | Out-String
    } finally {
      $ErrorActionPreference = $previousPreference
    }
    $AuthExit = $LASTEXITCODE
    $Summary.token_probe_ok = ($AuthExit -eq 0)
    Write-Log ("Token probe exit=" + $AuthExit)
    if ($AuthExit -eq 1) {
      Send-AutomationAlert "Bilibili access token was rejected or the open-platform API is unreachable; re-run scripts/configure_bilibili.ps1 before 08:00. Probe output: $AuthOutput"
    }
  }

  $VerifyJson = Join-Path $RunDir "verify-result.json"
  Run-Logged "Verifying run package: $RunDir" $VerifyJson @("-3", "-m", "briefing", "verify-run", "--run-dir", $RunDir)
  $Summary.verified = $true

  $PreflightArgs = @("-3", "-m", "briefing", "bilibili-preflight", "--run-dir", $RunDir)
  if ($Tid -gt 0) {
    $PreflightArgs += @("--tid", [string]$Tid)
  }
  if ($ScheduleAt) {
    $PreflightArgs += @("--schedule-at", $ScheduleAt)
  }
  $PreflightJson = Join-Path $RunDir "bilibili-preflight-result.json"
  Run-Logged "Running Bilibili preflight." $PreflightJson $PreflightArgs
  $Preflight = Get-Content -LiteralPath $PreflightJson -Raw -Encoding UTF8 | ConvertFrom-Json
  $Summary.preflight_ok = [bool]$Preflight.ok
  $Summary.ready_to_upload = [bool]$Preflight.ready_to_upload

  $PublishArgs = @("-3", "-m", "briefing", "bilibili-publish", "--run-dir", $RunDir)
  if ($Tid -gt 0) {
    $PublishArgs += @("--tid", [string]$Tid)
  }
  if ($ScheduleAt) {
    $PublishArgs += @("--schedule-at", $ScheduleAt)
  }
  $DryRunJson = Join-Path $RunDir "bilibili-publish-dry-run.json"
  Run-Logged "Running Bilibili publish dry-run." $DryRunJson $PublishArgs
  $Summary.dry_run_ok = $true

  # Multi-platform derivatives (9:16 vertical + WeChat article draft) come
  # from the verified package and never touch the attested upload payload.
  # Failures here are logged but must not block the Bilibili path.
  try {
    Run-Logged "Building vertical and WeChat repurpose outputs." (Join-Path $RunDir "repurpose-result.json") @("-3", "-m", "briefing", "repurpose", "--run-dir", $RunDir)
    $Summary.repurposed = $true
  } catch {
    $Summary.repurposed = $false
    Write-Log ("Repurpose step failed (non-fatal): " + $_.Exception.Message)
  }

  if ($Publish -and $env:BRIEFING_AUTO_PUBLISH -eq "1") {
    if (-not $Summary.ready_to_upload) {
      throw "publish requested but preflight is not ready"
    }
    $ExecuteJson = Join-Path $RunDir "bilibili-publish-result.json"
    Run-Logged "Auto publish enabled; executing Bilibili publish." $ExecuteJson @($PublishArgs + "--execute")
    $Summary.published = $true
    $Notice = [ordered]@{
      status = "published"
      run_date = $RunDate
      title = ""
      schedule_at = $(if ($ScheduleAt) { $ScheduleAt } else { $null })
    }
    try {
      $BiliJson = Get-Content -LiteralPath (Join-Path $RunDir "bilibili.json") -Raw -Encoding UTF8 | ConvertFrom-Json
      $Notice.title = [string]$BiliJson.title
    } catch {
      Write-Log ("Could not read bilibili.json for the success notice: " + $_.Exception.Message)
    }
    try {
      $UploadResult = Get-Content -LiteralPath (Join-Path $RunDir "bilibili-upload-result.json") -Raw -Encoding UTF8 | ConvertFrom-Json
      $Notice.upload_status = [string]$UploadResult.status
      if ($UploadResult.submit_response -and $UploadResult.submit_response.data) {
        $Notice.resource = $UploadResult.submit_response.data
      }
    } catch {
      Write-Log ("Could not read bilibili-upload-result.json for the success notice: " + $_.Exception.Message)
    }
    Send-AutomationNotice $Notice
  } elseif ($Publish) {
    Write-Log "Publish switch was provided, but BRIEFING_AUTO_PUBLISH is not 1; kept as dry-run only."
  }

  if ($env:BRIEFING_PRUNE_KEEP_DAYS -match '^\d+$' -and [int]$env:BRIEFING_PRUNE_KEEP_DAYS -gt 0) {
    try {
      Run-Logged "Pruning heavy media from old runs." (Join-Path $LogDir ("prune-runs-" + $RunDate + ".json")) @("-3", "-m", "briefing", "prune-runs", "--keep-days", $env:BRIEFING_PRUNE_KEEP_DAYS, "--execute")
    } catch {
      Write-Log ("Prune step failed (non-fatal): " + $_.Exception.Message)
    }
  }

  $Summary.completed_at = (Get-Date).ToString("s")
  Save-Summary $Summary
  Remove-Item -LiteralPath (Join-Path $LogDir ("attention-required-" + $RunDate + ".json")) -ErrorAction SilentlyContinue
  Write-Log "Daily briefing automation complete."
} catch {
  $Summary.error = [string]$_.Exception.Message
  $Summary.failed_at = (Get-Date).ToString("s")
  Save-Summary $Summary
  Write-Log ("Daily briefing automation failed: " + $Summary.error)
  Send-AutomationAlert $Summary.error
  throw
}
