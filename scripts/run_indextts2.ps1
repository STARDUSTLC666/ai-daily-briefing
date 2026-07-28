param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^[A-Za-z0-9_-]+$')]
  [string]$Profile,
  [string]$Date = "today",
  [ValidateSet("1080p", "4k")]
  [string]$Quality = "1080p",
  [int]$MaxItems = 8,
  [string]$IndexTTS2Root = $env:BRIEFING_INDEXTTS2_ROOT,
  [switch]$AllowNonPublishableProfile
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:BRIEFING_TTS_BACKEND = "indextts2"
$env:BRIEFING_TTS_PROFILE = $Profile
if (-not $IndexTTS2Root) {
  throw "Set BRIEFING_INDEXTTS2_ROOT or pass -IndexTTS2Root with the local IndexTTS2 checkout path."
}
$env:BRIEFING_INDEXTTS2_ROOT = (Resolve-Path -LiteralPath $IndexTTS2Root).Path
$env:BRIEFING_VOICE_PROFILE_DIR = Join-Path $env:BRIEFING_INDEXTTS2_ROOT "voice_profiles"
if ($AllowNonPublishableProfile) {
  $env:BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE = "1"
} else {
  Remove-Item Env:BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE -ErrorAction SilentlyContinue
}

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
& py -3 -m briefing run --date $Date --target bilibili --quality $Quality --max-items $MaxItems --workers 8
if ($LASTEXITCODE -ne 0) {
  throw "IndexTTS2 briefing run failed with exit code $LASTEXITCODE"
}
