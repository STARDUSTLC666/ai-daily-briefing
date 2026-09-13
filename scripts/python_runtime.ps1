function Resolve-BriefingPython {
  param([string]$Repo = (Split-Path -Parent $PSScriptRoot))

  # Validate candidates by actually running them. A registered py launcher or
  # Windows Store alias may exist even when its interpreter has been moved.
  $Candidates = @()
  if ($env:BRIEFING_PYTHON) {
    $Candidates += @{ Executable = $env:BRIEFING_PYTHON; Prefix = @() }
  }
  $Candidates += @{ Executable = (Join-Path $Repo '.venv\Scripts\python.exe'); Prefix = @() }
  $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($PythonCommand -and $PythonCommand.Source -notlike '*\WindowsApps\*') {
    $Candidates += @{ Executable = $PythonCommand.Source; Prefix = @() }
  }
  $InstallRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
  if (Test-Path -LiteralPath $InstallRoot) {
    foreach ($Directory in (Get-ChildItem -LiteralPath $InstallRoot -Directory | Sort-Object Name -Descending)) {
      $Candidates += @{ Executable = (Join-Path $Directory.FullName 'python.exe'); Prefix = @() }
    }
  }
  $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($Launcher) {
    foreach ($Version in @('-3', '-3.13', '-3.12', '-3.11')) {
      $Candidates += @{ Executable = $Launcher.Source; Prefix = @($Version) }
    }
  }
  foreach ($Candidate in $Candidates) {
    if (-not (Test-Path -LiteralPath $Candidate.Executable -PathType Leaf)) { continue }
    $PreviousPreference = $ErrorActionPreference
    try {
      $ErrorActionPreference = 'Continue'
      $Prefix = @($Candidate.Prefix)
      $Probe = & $Candidate.Executable @Prefix -c 'import sys; print("briefing-python-ok") if sys.version_info >= (3, 11) else sys.exit(1)' 2>$null
      if ($LASTEXITCODE -eq 0 -and $Probe -eq 'briefing-python-ok') { return $Candidate }
    } catch {
      # Continue to the next known interpreter, without altering the system.
    } finally {
      $ErrorActionPreference = $PreviousPreference
    }
  }
  throw 'No working Python 3.11+ found. Set BRIEFING_PYTHON to python.exe and run scripts/bootstrap.ps1.'
}
