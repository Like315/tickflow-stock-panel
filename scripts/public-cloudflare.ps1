param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$appEnvPath = Join-Path $repoRoot '.env'
$tunnelEnvPath = Join-Path $repoRoot '.env.cloudflare.local'
$authPath = Join-Path $repoRoot 'data\user_data\auth.json'
$composePath = Join-Path $repoRoot 'docker-compose.yml'
$cloudflareComposePath = Join-Path $repoRoot 'docker-compose.cloudflare.yml'

function Get-TickFlowEnvValue {
    param(
        [string]$Path,
        [string]$Name
    )
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    $line = Get-Content -LiteralPath $Path |
        Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } |
        Select-Object -Last 1
    if (-not $line) { return '' }
    return (($line -split '=', 2)[1]).Trim()
}

if (-not (Test-Path -LiteralPath $appEnvPath)) {
    throw '缺少 .env。请先复制 .env.example，并设置 AUTH_PASSWORD。'
}
if (-not (Test-Path -LiteralPath $tunnelEnvPath)) {
    throw '缺少 .env.cloudflare.local。请复制 .env.cloudflare.example，并填入 Tunnel token 和公网主机名。'
}

$adminPassword = Get-TickFlowEnvValue -Path $appEnvPath -Name 'AUTH_PASSWORD'
if (-not $adminPassword -and -not (Test-Path -LiteralPath $authPath)) {
    throw '尚未初始化 admin：请在 .env 设置至少 6 位 AUTH_PASSWORD。'
}

$localPortValue = Get-TickFlowEnvValue -Path $appEnvPath -Name 'PORT'
if (-not $localPortValue) { $localPortValue = '3018' }
$localPort = 0
if (-not [int]::TryParse($localPortValue, [ref]$localPort) -or $localPort -lt 1 -or $localPort -gt 65535) {
    throw '.env 中的 PORT 必须是 1 到 65535 之间的整数。'
}

$tunnelToken = Get-TickFlowEnvValue -Path $tunnelEnvPath -Name 'TUNNEL_TOKEN'
if ($tunnelToken.Length -lt 20) {
    throw '.env.cloudflare.local 中缺少有效的 TUNNEL_TOKEN。'
}
$publicHostname = Get-TickFlowEnvValue -Path $tunnelEnvPath -Name 'PUBLIC_HOSTNAME'
if ($publicHostname -notmatch '^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$') {
    throw 'PUBLIC_HOSTNAME 格式无效；请只填写类似 stocks.example.com 的主机名。'
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw '未找到 Docker。请先安装 Docker Desktop，并确认 docker compose 可用。'
}

$composeArgs = @(
    'compose',
    '-f', $composePath,
    '-f', $cloudflareComposePath
)

Push-Location $repoRoot
try {
    & docker @composeArgs config --quiet
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose 配置校验失败。'
    }

    if ($SkipBuild) {
        & docker @composeArgs up -d
    } else {
        & docker @composeArgs up -d --build
    }
    if ($LASTEXITCODE -ne 0) {
        throw '应用或 Cloudflare Tunnel 容器启动失败。'
    }

    $localHealthy = $false
    foreach ($attempt in 1..30) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$localPort/health" -TimeoutSec 2
            if ($health.status -eq 'ok') {
                $localHealthy = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $localHealthy) {
        throw '应用在 60 秒内未通过本地 /health 检查，请查看 docker compose logs app。'
    }

    $tunnelRunning = (& docker inspect --format '{{.State.Running}}' TickFlow_Cloudflare_Tunnel 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $tunnelRunning -ne 'true') {
        throw 'cloudflared 容器未运行，请查看 docker compose logs cloudflared。'
    }

    $publicHealthUrl = "https://$publicHostname/health"
    $publicHealthy = $false
    foreach ($attempt in 1..30) {
        try {
            $health = Invoke-RestMethod -Uri $publicHealthUrl -TimeoutSec 5
            if ($health.status -eq 'ok') {
                $publicHealthy = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $publicHealthy) {
        throw "Tunnel 已启动，但 $publicHealthUrl 尚不可用。请检查控制台 Published application 是否指向 http://app:3018。"
    }

    Write-Host "公网面板已就绪：https://$publicHostname"
    & docker @composeArgs ps
} finally {
    Pop-Location
}
