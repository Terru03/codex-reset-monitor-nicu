param(
    [string]$Destination = "$env:USERPROFILE\Documents\AI\codex-reset-monitor-nicu"
)

$ErrorActionPreference = "Stop"

$sourceRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$nicuRepo = "Terru03/codex-reset-monitor-nicu"
$nicuAuthHome = ".codex-reset-monitor-auth-nicu"

function New-StrongToken {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = New-Object byte[] 32
        $rng.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+","-").Replace("/","_")
    }
    finally {
        $rng.Dispose()
    }
}

Write-Host "Preparing Nicu monitor in $Destination"

if (-not (Test-Path $Destination)) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
}

# Refresh monitor files from the working David repo while preserving Nicu's own Git metadata.
Get-ChildItem -LiteralPath $sourceRoot -Force |
    Where-Object { $_.Name -notin @(".git", "discord-worker") } |
    ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }

$workflowPath = Join-Path $Destination ".github\workflows\monitor.yml"
$workflow = Get-Content -LiteralPath $workflowPath -Raw
$workflow = $workflow.Replace("ACCOUNT_LABEL: David", "ACCOUNT_LABEL: Nicu")
$workflow = $workflow.Replace("ACCOUNT_SLUG: david", "ACCOUNT_SLUG: nicu")
$workflow = $workflow.Replace("group: codex-reset-monitor", "group: codex-reset-monitor-nicu")
Set-Content -LiteralPath $workflowPath -Value $workflow -Encoding UTF8

$statePath = Join-Path $Destination "state\reset-state.json"
if (-not (Test-Path (Join-Path $Destination ".git"))) {
@'
{
  "version": 1,
  "windows": {}
}
'@ | Set-Content -LiteralPath $statePath -Encoding UTF8
}

$distros = (wsl.exe -l -q) -replace [char]0,"" | Where-Object { $_ -and $_ -notmatch "docker-desktop" }
$codexDistro = $null
foreach ($d in $distros) {
    $check = wsl.exe -d $d -- sh -lc 'command -v bash >/dev/null 2>&1 && command -v codex >/dev/null 2>&1 && command -v openssl >/dev/null 2>&1 && echo OK'
    if ($check -match "OK") {
        $codexDistro = $d.Trim()
        break
    }
}
if (-not $codexDistro) {
    throw "Could not find a WSL distro containing bash, codex, and openssl."
}

$authCheck = wsl.exe -d $codexDistro -- bash -lc ('test -f "$HOME/' + $nicuAuthHome + '/auth.json" && echo OK')
if ($authCheck -match "OK") {
    Write-Host ""
    Write-Host "Existing isolated Nicu Codex login found. Reusing it."
}
else {
    Write-Host ""
    Write-Host "Starting isolated Codex login for Nicu."
    Write-Host "Complete the device login with NICU'S OpenAI account."

    $loginCmd = 'mkdir -p "$HOME/' + $nicuAuthHome + '" && CODEX_HOME="$HOME/' + $nicuAuthHome + '" codex login --device-auth'
    wsl.exe -d $codexDistro -- bash -lc $loginCmd
    if ($LASTEXITCODE -ne 0) {
        throw "Nicu Codex login failed."
    }

    $authCheck = wsl.exe -d $codexDistro -- bash -lc ('test -f "$HOME/' + $nicuAuthHome + '/auth.json" && echo OK')
    if ($authCheck -notmatch "OK") {
        throw "Nicu isolated auth.json was not created."
    }
}

# Convert C:\path\to\folder to /mnt/c/path/to/folder without passing backslashes through wsl.exe.
$fullDestination = [System.IO.Path]::GetFullPath($Destination)
if ($fullDestination -notmatch '^(?<drive>[A-Za-z]):\\(?<rest>.*)$') {
    throw "Destination must be a local Windows drive path. Got: $fullDestination"
}

$driveLetter = $matches['drive'].ToLowerInvariant()
$relativePath = $matches['rest'] -replace '\\','/'
$destWsl = "/mnt/$driveLetter/$relativePath"

Write-Host "WSL project path: $destWsl"

$probeCmd = 'cd "' + $destWsl + '" && CODEX_HOME="$HOME/' + $nicuAuthHome + '" python3 ./scripts/local_probe.py'
Write-Host ""
Write-Host "Verifying Nicu Codex usage..."
wsl.exe -d $codexDistro -- bash -lc $probeCmd
if ($LASTEXITCODE -ne 0) {
    throw "Nicu usage probe failed."
}

$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $bytes = New-Object byte[] 32
    $rng.GetBytes($bytes)
    $authFileKey = [Convert]::ToBase64String($bytes)
}
finally {
    $rng.Dispose()
}

$encryptCmd = 'export AUTH_FILE_KEY=''' + $authFileKey + '''; openssl enc -aes-256-cbc -pbkdf2 -salt -in "$HOME/' + $nicuAuthHome + '/auth.json" -out "' + $destWsl + '/secrets/auth.json.enc" -pass env:AUTH_FILE_KEY'
wsl.exe -d $codexDistro -- bash -lc $encryptCmd
if ($LASTEXITCODE -ne 0) {
    throw "Failed to encrypt Nicu auth."
}

$nicuIngestToken = New-StrongToken
$workerDir = Join-Path $sourceRoot "discord-worker"

Write-Host ""
Write-Host "Rotating Nicu's private Worker ingest token..."
Push-Location $workerDir
try {
    $nicuIngestToken | npx wrangler secret put INGEST_TOKEN_NICU
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install INGEST_TOKEN_NICU in Cloudflare Worker."
    }
}
finally {
    Pop-Location
}

$secureDiscordWebhook = Read-Host "Paste the SAME Discord webhook URL used by David (hidden)" -AsSecureString
$discordWebhook = [System.Net.NetworkCredential]::new("", $secureDiscordWebhook).Password

$discordUserId = Read-Host "Paste your numeric Discord User ID"
if ($discordUserId -notmatch '^\d{17,20}$') {
    throw "Discord User ID is not valid."
}

Set-Location $Destination

if (-not (Test-Path ".git")) {
    git init -b main
    if ($LASTEXITCODE -ne 0) {
        throw "git init failed."
    }
}

git add .
if ($LASTEXITCODE -ne 0) {
    throw "git add failed."
}

$staged = git diff --cached --name-only
if ($staged) {
    git commit -m "Initial Nicu Codex reset monitor"
    if ($LASTEXITCODE -ne 0) {
        throw "git commit failed."
    }
}

$repoExists = $false
gh repo view $nicuRepo *> $null
if ($LASTEXITCODE -eq 0) {
    $repoExists = $true
}

if (-not $repoExists) {
    gh repo create codex-reset-monitor-nicu --public --source . --remote origin --push
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create/push Nicu GitHub repository."
    }
}
else {
    Write-Host "Nicu GitHub repository already exists. Updating it."

    $origin = git remote get-url origin 2>$null
    if (-not $origin) {
        git remote add origin "https://github.com/$nicuRepo.git"
    }

    git push -u origin main
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to push Nicu repository."
    }
}

gh secret set AUTH_FILE_KEY -R $nicuRepo --body $authFileKey
if ($LASTEXITCODE -ne 0) {
    throw "Failed to set AUTH_FILE_KEY."
}

gh secret set DISCORD_WEBHOOK_URL -R $nicuRepo --body $discordWebhook
if ($LASTEXITCODE -ne 0) {
    throw "Failed to set DISCORD_WEBHOOK_URL."
}

gh secret set DISCORD_USER_ID -R $nicuRepo --body $discordUserId
if ($LASTEXITCODE -ne 0) {
    throw "Failed to set DISCORD_USER_ID."
}

gh secret set STATUS_INGEST_TOKEN -R $nicuRepo --body $nicuIngestToken
if ($LASTEXITCODE -ne 0) {
    throw "Failed to set STATUS_INGEST_TOKEN."
}

Write-Host ""
Write-Host "Triggering Nicu's first GitHub check..."
gh workflow run monitor.yml -R $nicuRepo
if ($LASTEXITCODE -ne 0) {
    throw "Failed to trigger Nicu workflow."
}

Start-Sleep -Seconds 5

$runId = gh run list -R $nicuRepo --workflow monitor.yml --limit 1 --json databaseId --jq '.[0].databaseId'
if ($runId) {
    gh run watch $runId -R $nicuRepo --exit-status
    if ($LASTEXITCODE -ne 0) {
        throw "Nicu workflow failed."
    }
}

Write-Host ""
Write-Host "Nicu monitor deployed successfully."
Write-Host "Repository: https://github.com/$nicuRepo"
Write-Host "Isolated Codex home: ~/$nicuAuthHome"
