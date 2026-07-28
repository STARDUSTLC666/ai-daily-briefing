param(
  [string]$CredentialPath = ""
)

$ErrorActionPreference = "Stop"

if (-not $CredentialPath) {
  $Repo = Split-Path -Parent $PSScriptRoot
  $CredentialPath = Join-Path $Repo ".local\bilibili-credentials.clixml"
}

if (-not (Test-Path -LiteralPath $CredentialPath)) {
  return
}

$Config = Import-Clixml -LiteralPath $CredentialPath
$Required = @("ClientId", "ClientSecret", "AccessToken", "Tid")
foreach ($Name in $Required) {
  if (-not $Config.PSObject.Properties[$Name] -or $null -eq $Config.$Name) {
    throw "Invalid Bilibili credential file: missing $Name"
  }
}

function ConvertFrom-LocalSecureString([Security.SecureString]$Value) {
  if ($null -eq $Value) {
    return ""
  }
  return [System.Net.NetworkCredential]::new("", $Value).Password
}

$env:BILI_CLIENT_ID = ConvertFrom-LocalSecureString $Config.ClientId
$env:BILI_CLIENT_SECRET = ConvertFrom-LocalSecureString $Config.ClientSecret
$env:BILI_ACCESS_TOKEN = ConvertFrom-LocalSecureString $Config.AccessToken
$env:BILI_TID = [string][int]$Config.Tid
$env:BRIEFING_AUTO_PUBLISH = if ([bool]$Config.AutoPublish) { "1" } else { "0" }
if ($Config.PSObject.Properties["TokenIssuedAt"] -and $Config.TokenIssuedAt) {
  $env:BILI_TOKEN_ISSUED_AT = [string]$Config.TokenIssuedAt
}
if ($Config.PSObject.Properties["TokenTtlDays"] -and $Config.TokenTtlDays) {
  $env:BILI_TOKEN_TTL_DAYS = [string][int]$Config.TokenTtlDays
}
