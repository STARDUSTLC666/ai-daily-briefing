param([string]$Python = '', [switch]$SkipBrowser)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'python_runtime.ps1')
if ($Python) { $env:BRIEFING_PYTHON = $Python }
$Runtime = Resolve-BriefingPython -Repo $Repo
$Prefix = @($Runtime.Prefix)
$Venv = Join-Path $Repo '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
  & $Runtime.Executable @Prefix -m venv $Venv
  if ($LASTEXITCODE -ne 0) { throw 'Could not create the project Python environment.' }
}
& $VenvPython -m pip install -e $Repo
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
& npm.cmd --prefix (Join-Path $Repo 'remotion') ci --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Remotion dependency installation failed.' }
if (-not $SkipBrowser) {
  $BrowserChannel = & $VenvPython -c 'from briefing.browser_runtime import crawl_channel; print(crawl_channel())'
  if ($LASTEXITCODE -ne 0) { throw 'Browser runtime detection failed.' }
  if ($BrowserChannel -eq 'chromium') {
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Browser installation failed. Install Chrome/Edge or retry when the network is available.' }
  } else {
    Write-Output "Using installed browser channel: $BrowserChannel"
  }
}
Write-Output 'Project runtime ready. Use scripts/briefing.ps1 doctor to check production prerequisites.'
