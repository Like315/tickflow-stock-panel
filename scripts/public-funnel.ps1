param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envPath = Join-Path $repoRoot '.env'
$authPath = Join-Path $repoRoot 'data\user_data\auth.json'

function Get-TickFlowEnvValue {
    param([string]$Name)
    if (-not (Test-Path -LiteralPath $envPath)) { return '' }
    $line = Get-Content -LiteralPath $envPath |
        Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } |
        Select-Object -Last 1
    if (-not $line) { return '' }
    return (($line -split '=', 2)[1]).Trim()
}

if (-not (Test-Path -LiteralPath $envPath)) {
    throw '缺少 .env。请先复制 .env.example 为 .env，并设置 AUTH_PASSWORD。'
}

$adminPassword = Get-TickFlowEnvValue -Name 'AUTH_PASSWORD'
if (-not $adminPassword -and -not (Test-Path -LiteralPath $authPath)) {
    throw '尚未初始化 admin：请在 .env 设置至少 6 位 AUTH_PASSWORD。'
}

$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscale) {
    $candidate = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path -LiteralPath $candidate) {
        $tailscalePath = $candidate
    } else {
        throw '未找到 Tailscale CLI。请先安装 Tailscale、登录，并在管理台启用 Funnel。'
    }
} else {
    $tailscalePath = $tailscale.Source
}

Push-Location $repoRoot
try {
    if ($SkipBuild) {
        docker compose up -d
    } else {
        docker compose up -d --build
    }

    $healthy = $false
    foreach ($attempt in 1..30) {
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:3018/health' -TimeoutSec 2
            if ($health.status -eq 'ok') {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $healthy) {
        throw '应用在 60 秒内未通过 /health 检查，请运行 docker compose logs app。'
    }

    & $tailscalePath status | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Tailscale 尚未登录或未连接，请先打开 Tailscale 客户端完成登录。'
    }

    & $tailscalePath funnel --bg --yes 3018
    if ($LASTEXITCODE -ne 0) {
        throw 'Funnel 启动失败。首次使用请按浏览器提示批准 Funnel 权限。'
    }
    & $tailscalePath funnel status
} finally {
    Pop-Location
}
