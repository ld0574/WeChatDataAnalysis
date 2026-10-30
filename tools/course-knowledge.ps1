param(
    [ValidateSet('Status','Inventory','Prepare','Resources','Run','Build','Serve','Pause','Resume','Search','Ask','Review')]
    [string]$Mode = 'Status',
    [string]$Source = 'D:\迁移\知乎 AI 大模型应用开发',
    [string]$Query = '',
    [string]$VideoId = '',
    [string]$Model = 'qwen3.5:9b',
    [int]$Port = 3002
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$archiveRoot = Join-Path $projectRoot 'output\course-knowledge'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
if ($Mode -eq 'Build') {
    if (!(Test-Path -LiteralPath (Join-Path $archiveRoot 'inventory.json'))) {
        & $PSCommandPath -Mode Inventory -Source $Source -Model $Model
    }
    foreach ($step in @('Resources','Prepare','Run')) {
        & $PSCommandPath -Mode $step -Source $Source -Model $Model
    }
    return
}
$arguments = @('-m','wechat_decrypt_tool.course_knowledge','--root',$archiveRoot,'--model',$Model)
switch ($Mode) {
    'Status'    { $arguments += 'status' }
    'Inventory' { $arguments += @('inventory',$Source) }
    'Prepare'   { $arguments += @('prepare','--inventory',(Join-Path $archiveRoot 'inventory.json')) }
    'Resources' { $arguments += @('courseware',$Source) }
    'Run' {
        $inventoryPath = Join-Path $archiveRoot 'inventory-unique.json'
        if (!(Test-Path -LiteralPath $inventoryPath)) { throw '请先运行 Prepare，完成文件内容去重。' }
        $arguments += @('run','--inventory',$inventoryPath,'--asr-model',(Join-Path $projectRoot 'voice_models\qwen3-asr-06b-hf'),'--limit','0','--enrich')
    }
    'Serve' { $arguments += @('serve','--port',"$Port") }
    'Pause' { Set-Content -LiteralPath (Join-Path $archiveRoot 'pause.flag') -Value 'pause'; return }
    'Resume' { $arguments += 'resume' }
    'Search' { $arguments += @('search',$Query) }
    'Ask' {
        $arguments += @('ask',$Query)
        if ($VideoId) { $arguments += @('--video-id',$VideoId) }
    }
    'Review' {
        if (!$VideoId) { throw 'Review 需要 -VideoId，课程编号可从 Status 或本地页面查看。' }
        $arguments += @('review',$VideoId)
    }
}
& $pythonPath @arguments
if ($LASTEXITCODE -ne 0) { throw "课程归档命令失败，退出码 $LASTEXITCODE；已提交的片段可以恢复。" }
