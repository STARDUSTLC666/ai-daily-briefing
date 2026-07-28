param(
  [string]$Profile = "daily-briefing",
  [string]$AccountFile = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$Repo = Split-Path -Parent $PSScriptRoot
if (-not $AccountFile) {
  $AccountFile = Join-Path $Repo ".local\bilibili-account.json"
}
if (-not (Test-Path -LiteralPath $AccountFile)) {
  throw "Bilibili account fingerprint is missing: $AccountFile"
}
if (-not (Get-Command opencli -ErrorAction SilentlyContinue)) {
  throw "opencli is required to verify the dedicated Bilibili browser profile"
}

$Expected = Get-Content -LiteralPath $AccountFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$Expected.profile -ne $Profile -or -not $Expected.account_id) {
  throw "Bilibili account fingerprint does not match profile '$Profile'"
}

$Raw = & opencli --profile $Profile bilibili whoami -f json --window background --site-session persistent
if ($LASTEXITCODE -ne 0) {
  throw "Cannot read the logged-in Bilibili account for profile '$Profile'"
}
$Actual = $Raw | ConvertFrom-Json
$Matches = [bool]$Actual.logged_in -and
  ([string]$Actual.id -eq [string]$Expected.account_id) -and
  ([string]$Actual.username -eq [string]$Expected.username)

$Result = [ordered]@{
  ok = $Matches
  logged_in = [bool]$Actual.logged_in
  profile = $Profile
  username = [string]$Actual.username
}
$Result | ConvertTo-Json -Compress
if (-not $Matches) {
  throw "Dedicated Bilibili browser profile is logged out or points to a different account"
}
