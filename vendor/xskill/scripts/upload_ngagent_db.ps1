<#
.SYNOPSIS
  把本机 ngagent 的 SQLite db 上传到 xskill team server 入库（免 scp / 免密码）。

.DESCRIPTION
  企业 Windows 用户大多没有 sshpass、也不愿手敲 scp 密码。本脚本改走 HTTP：
  直接把 ngagent.db POST 到 team server 的 /api/v1/team/ingest-db 端点，
  服务器落盘后自动桥接入库、出 skill。

  流程：
    1. 用 join token 调 /api/v1/team/register 拿一个 client_id（首次），
       存到 %USERPROFILE%\.xskill_client_id 复用。
    2. 把 db 文件 multipart POST 到 /api/v1/team/ingest-db。

  兼容 Windows PowerShell 5.1 与 PowerShell 7+（5.1 手工拼 multipart，
  7+ 直接用 -Form）。

.PARAMETER Server
  team server 的 HTTP 地址，形如 http://HOST:PORT。
  注意：是 xskill serve 监听的 HTTP 端口（默认 8000），不是 ssh 的 9960。

.PARAMETER Token
  join token（服务器 `xskill serve --server` 启动时打印）。

.PARAMETER DbPath
  ngagent db 路径。默认 %USERPROFILE%\.local\share\opencode\db\ngagent.db。

.PARAMETER Eco
  生态 id，默认 ngagent。

.EXAMPLE
  .\upload_ngagent_db.ps1 -Server http://7.220.144.233:8000 -Token secret-token
#>
param(
    [Parameter(Mandatory = $true)] [string]$Server,
    [Parameter(Mandatory = $true)] [string]$Token,
    [string]$DbPath = "$env:USERPROFILE\.local\share\opencode\db\ngagent.db",
    [string]$Eco = "ngagent"
)

$ErrorActionPreference = "Stop"
$Server = $Server.TrimEnd('/')

if (-not (Test-Path $DbPath)) {
    Write-Error "找不到 db 文件: $DbPath （用 -DbPath 指定实际路径）"
    exit 2
}

# ── 1. 拿 client_id（首次 register，之后复用本地缓存）──────────────
$idFile = "$env:USERPROFILE\.xskill_client_id"
if (Test-Path $idFile) {
    $ClientId = (Get-Content $idFile -Raw).Trim()
    Write-Host "复用已注册 client_id: $ClientId"
} else {
    $regBody = @{ token = $Token; client_label = $env:COMPUTERNAME; hostname = $env:COMPUTERNAME } | ConvertTo-Json
    $reg = Invoke-RestMethod -Uri "$Server/api/v1/team/register" -Method Post `
        -ContentType "application/json" -Body $regBody
    $ClientId = $reg.client_id
    Set-Content -Path $idFile -Value $ClientId -NoNewline
    Write-Host "已注册，client_id: $ClientId"
}

$headers = @{ "X-Xskill-Token" = $Token; "X-Xskill-Client" = $ClientId }
$uri = "$Server/api/v1/team/ingest-db"

# ── 2. 上传 db（multipart）─────────────────────────────────────────
if ($PSVersionTable.PSVersion.Major -ge 7) {
    # PowerShell 7+：原生 -Form 直接传文件
    $form = @{
        eco  = $Eco
        file = Get-Item -Path $DbPath
    }
    $resp = Invoke-RestMethod -Uri $uri -Method Post -Headers $headers -Form $form
} else {
    # Windows PowerShell 5.1：手工拼 multipart/form-data
    $boundary = [System.Guid]::NewGuid().ToString()
    $LF = "`r`n"
    $fileBytes = [System.IO.File]::ReadAllBytes($DbPath)
    $fileEnc = [System.Text.Encoding]::GetEncoding("ISO-8859-1").GetString($fileBytes)
    $fileName = [System.IO.Path]::GetFileName($DbPath)
    $body = (
        "--$boundary", "Content-Disposition: form-data; name=`"eco`"", "", $Eco,
        "--$boundary",
        "Content-Disposition: form-data; name=`"file`"; filename=`"$fileName`"",
        "Content-Type: application/octet-stream", "", $fileEnc,
        "--$boundary--", ""
    ) -join $LF
    $resp = Invoke-RestMethod -Uri $uri -Method Post -Headers $headers `
        -ContentType "multipart/form-data; boundary=$boundary" -Body $body
}

Write-Host "上传成功：bridged $($resp.bridged) 条轨迹（client=$($resp.client_id)）"
Write-Host "服务器已落盘并入库，watcher 将自动出 skill。"
