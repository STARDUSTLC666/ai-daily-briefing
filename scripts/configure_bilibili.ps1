param(
  [ValidateRange(1, 9999)]
  [int]$Tid = 231,
  [ValidateRange(1, 365)]
  [int]$TokenTtlDays = 30,
  [switch]$EnableAutoPublish,
  [switch]$DisableAutoPublish,
  [switch]$ShowStatus
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$LocalDir = Join-Path $Repo ".local"
$CredentialPath = Join-Path $LocalDir "bilibili-credentials.clixml"

function Test-NonEmptySecureString([Security.SecureString]$Value) {
  return $null -ne $Value -and $Value.Length -gt 0
}

if ($ShowStatus) {
  if (-not (Test-Path -LiteralPath $CredentialPath)) {
    Write-Output "Bilibili credentials: not configured"
    Write-Output "Credential file: $CredentialPath"
    exit 1
  }
  $Config = Import-Clixml -LiteralPath $CredentialPath
  Write-Output "Bilibili credentials: configured for the current Windows user"
  Write-Output "TID: $($Config.Tid)"
  Write-Output "Automatic upload enabled: $([bool]$Config.AutoPublish)"
  Write-Output "Credential file: $CredentialPath"
  exit 0
}

if ($EnableAutoPublish -and $DisableAutoPublish) {
  throw "Choose either -EnableAutoPublish or -DisableAutoPublish, not both."
}

New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null

$Existing = $null
if (Test-Path -LiteralPath $CredentialPath) {
  $Existing = Import-Clixml -LiteralPath $CredentialPath
}

Write-Output "Bilibili Open Platform credentials will be encrypted with Windows DPAPI."
Write-Output "They can only be decrypted by the current Windows user on this computer."

$ClientId = Read-Host "BILI_CLIENT_ID" -AsSecureString
$ClientSecret = Read-Host "BILI_CLIENT_SECRET" -AsSecureString
$AccessToken = Read-Host "BILI_ACCESS_TOKEN" -AsSecureString

if (-not (Test-NonEmptySecureString $ClientId) -or
    -not (Test-NonEmptySecureString $ClientSecret) -or
    -not (Test-NonEmptySecureString $AccessToken)) {
  throw "Client ID, client secret, and access token must all be provided."
}

$AutoPublish = if ($EnableAutoPublish) {
  $true
} elseif ($DisableAutoPublish) {
  $false
} elseif ($null -ne $Existing) {
  [bool]$Existing.AutoPublish
} else {
  $false
}

$Config = [pscustomobject]@{
  Version = 2
  ClientId = $ClientId
  ClientSecret = $ClientSecret
  AccessToken = $AccessToken
  Tid = $Tid
  AutoPublish = $AutoPublish
  # Expiry bookkeeping: the open platform has no queryable token expiry, so we
  # record when this token was saved and its assumed TTL; preflight warns
  # three days before the estimate runs out.
  TokenIssuedAt = (Get-Date).ToString("o")
  TokenTtlDays = $TokenTtlDays
  UpdatedAt = (Get-Date).ToString("o")
  WindowsUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
}

$Config | Export-Clixml -LiteralPath $CredentialPath -Force

# Verify that the current user can decrypt the newly written values without
# printing any credential material.
. (Join-Path $PSScriptRoot "load_bilibili_credentials.ps1") -CredentialPath $CredentialPath
$Missing = @("BILI_CLIENT_ID", "BILI_CLIENT_SECRET", "BILI_ACCESS_TOKEN") |
  Where-Object { -not [Environment]::GetEnvironmentVariable($_, "Process") }
if ($Missing) {
  throw "Credential verification failed: $($Missing -join ', ')"
}

Write-Output "Bilibili credentials saved and verified."
Write-Output "TID: $Tid"
Write-Output "Automatic upload enabled: $AutoPublish"
Write-Output "Credential file: $CredentialPath"
