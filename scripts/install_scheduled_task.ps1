param(
  [string]$TaskName = "AI-Daily-Briefing",
  [string]$ReviewTaskName = "",
  [string]$GenerateTaskName = "",
  [string]$PublishTaskName = "",
  [string]$CheckTaskName = "",
  [string]$ReviewAt = "00:00",
  [string]$GenerateAt = "07:00",
  [string]$PublishAt = "08:00",
  [string]$CheckAt = "08:20",
  [string]$RunAt = "",
  [string]$Quality = "1080p",
  [int]$MaxItems = 0,
  [int]$MaxNewItems = 3,
  [int]$Tid = 0,
  [string]$ScheduleAt = "",
  [switch]$Publish,
  [switch]$Enable,
  [switch]$SingleTask,
  [switch]$ReviewWorkflow
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $PSScriptRoot "run_daily.ps1"

if (-not (Test-Path -LiteralPath $RunScript)) {
  throw "run_daily.ps1 not found: $RunScript"
}

if (-not $GenerateTaskName) {
  $GenerateTaskName = "$TaskName-Generate"
}
if (-not $ReviewTaskName) {
  $ReviewTaskName = "$TaskName-Review"
}
if (-not $PublishTaskName) {
  $PublishTaskName = "$TaskName-Publish"
}
if (-not $CheckTaskName) {
  $CheckTaskName = "$TaskName-Check"
}
if ($RunAt) {
  $GenerateAt = $RunAt
}

function New-BriefingActionArgs([string]$Mode, [bool]$PublishTask) {
  $ActionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" -Mode $Mode -Quality `"$Quality`" -MaxItems $MaxItems -MaxNewItems $MaxNewItems"
  if ($Tid -gt 0) {
    $ActionArgs += " -Tid $Tid"
  }
  if ($PublishTask -and $ScheduleAt) {
    $ActionArgs += " -ScheduleAt `"$ScheduleAt`""
  }
  if ($PublishTask) {
    $ActionArgs += " -Publish"
  }
  return $ActionArgs
}

# Prefer PowerShell 7 (pwsh) when installed: its UTF-8 defaults and native-command
# error handling are what run_daily.ps1 is primarily exercised under; Windows
# PowerShell 5.1 remains the fallback so the task still works everywhere.
$PowerShellExe = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh.exe" } else { "powershell.exe" }

function Register-BriefingTask([string]$Name, [string]$At, [string]$Mode, [bool]$PublishTask) {
  $ActionArgs = New-BriefingActionArgs $Mode $PublishTask
  $Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $ActionArgs -WorkingDirectory $Repo
  $Trigger = New-ScheduledTaskTrigger -Daily -At $At
  $Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)
  $Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

  Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
  if ($Enable) {
    Enable-ScheduledTask -TaskName $Name | Out-Null
  } else {
    Disable-ScheduledTask -TaskName $Name | Out-Null
  }

  Write-Output "Scheduled task installed: $Name daily at $At"
  Write-Output "Mode: $Mode"
  Write-Output "Enabled: $([bool]$Enable)"
  Write-Output "Action: $PowerShellExe $ActionArgs"
}

if ($ReviewWorkflow -and -not $SingleTask) {
  Register-BriefingTask $ReviewTaskName $ReviewAt "prepare-review" $false
  Register-BriefingTask $GenerateTaskName $GenerateAt "morning-render" ([bool]$Publish)
  Register-BriefingTask $CheckTaskName $CheckAt "check" $false
} elseif (-not $SingleTask) {
  # Keep rendering and upload in separate task instances. The 08:00 task
  # re-runs every immutable gate against the 07:00 package before upload.
  Register-BriefingTask $GenerateTaskName $GenerateAt "generate" $false
  Register-BriefingTask $PublishTaskName $PublishAt "publish" ([bool]$Publish)
} else {
  Register-BriefingTask $TaskName $GenerateAt "auto" ([bool]$Publish)
}

Write-Output "Script: $RunScript"
Write-Output "Workflow: $(if ($ReviewWorkflow -and -not $SingleTask) { 'review-assisted' } elseif ($SingleTask) { 'automatic-single-task' } else { 'automatic-split-generate-publish' })"
Write-Output "Publish switch: $([bool]$Publish). ScheduleAt: $ScheduleAt. Real upload still requires BRIEFING_AUTO_PUBLISH=1 and Bilibili credentials."
