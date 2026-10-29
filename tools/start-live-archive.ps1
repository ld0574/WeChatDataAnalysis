param([switch]$WithFrontend)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONPATH = Join-Path $projectRoot 'src'
if (-not $env:WCE_NATIVE_CORE_SOURCE_DIR) {
    $installedNative = 'D:\Program Files\WeChatDataAnalysis\resources\backend\native'
    if (Test-Path -LiteralPath $installedNative) { $env:WCE_NATIVE_CORE_SOURCE_DIR = $installedNative }
}
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if ($WithFrontend) {
    $env:WECHAT_TOOL_PORT = '10393'
    $frontendPort = Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue
    if (-not $frontendPort) {
        $nodePath = (Get-Command node -ErrorAction Stop).Source
        Start-Process -FilePath $nodePath -ArgumentList @('node_modules\nuxt\bin\nuxt.mjs','dev','--port','3001','--host','127.0.0.1') -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden
    }
    Write-Host '归档与搜索页面：http://127.0.0.1:3001/archive'
}
if (Get-NetTCPConnection -LocalPort 10393 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host '本地后端已经在运行。'
    exit 0
}
& $pythonPath -m uvicorn wechat_decrypt_tool.api:app --host 127.0.0.1 --port 10393
exit $LASTEXITCODE
