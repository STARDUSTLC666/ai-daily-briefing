param([Parameter(ValueFromRemainingArguments = $true)][string[]]$BriefingArgs)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'python_runtime.ps1')
$Runtime = Resolve-BriefingPython -Repo $Repo
$Prefix = @($Runtime.Prefix)
$env:PYTHONIOENCODING = 'utf-8'
Push-Location -LiteralPath $Repo
try {
  & $Runtime.Executable @Prefix -m briefing @BriefingArgs
  $BriefingExitCode = $LASTEXITCODE
} finally {
  Pop-Location
}
exit $BriefingExitCode
