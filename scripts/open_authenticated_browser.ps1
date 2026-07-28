param(
    [string]$Url = "https://x.com/",
    [string]$ProfileDirectory = "Default",
    [string]$OpenCliProfile = "daily-briefing",
    [string]$ProfileRoot = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ProfileRoot) {
    $ProfileRoot = Join-Path $repoRoot ".local\opencli-chrome-profile"
}
$ProfileRoot = [System.IO.Path]::GetFullPath($ProfileRoot)
$chromeCandidates = @(
    @(
        $env:BRIEFING_CHROME,
        $env:CHROME,
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
)

if (-not $chromeCandidates) {
    throw "未找到 Chrome 或 Edge。可通过 BRIEFING_CHROME 指定浏览器路径。"
}

New-Item -ItemType Directory -Force -Path $ProfileRoot | Out-Null
$browser = $chromeCandidates[0]
$arguments = @(
    "--user-data-dir=$ProfileRoot",
    "--profile-directory=$ProfileDirectory",
    "--no-first-run",
    "--no-default-browser-check",
    $Url
)
Start-Process -FilePath $browser -ArgumentList $arguments

Write-Host "已打开日报专用浏览器配置：$ProfileRoot"
Write-Host "OpenCLI 配置别名：$OpenCliProfile"
Write-Host "请只在这个窗口登录 X 与 B站。每日任务运行期间应保持这个专用 Chrome 打开；不要在其中处理私人浏览内容。"
