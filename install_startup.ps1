param(
    [string]$InstallDir = "C:\ClaudeHardware",
    [string]$TaskName = "ClaudeHardwareCompanion",
    [ValidateSet("hardware", "test")]
    [string]$Mode = "hardware"
)

$ErrorActionPreference = "Stop"
$startupMethod = "TaskScheduler"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$sourceFiles = @(
    "claude_hardware_companion.py",
    "claude_hardware_companion_test.py",
    "send_event.ps1",
    "README.md",
    "requirements.txt",
    "settings.json",
    "main.ino"
)

foreach ($file in $sourceFiles) {
    $sourcePath = Join-Path $PSScriptRoot $file
    $destPath = Join-Path $InstallDir $file
    Copy-Item -Force -Path $sourcePath -Destination $destPath
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    throw "Python 3 未安装或不在 PATH 中。"
}

$requirementsOk = $true
try {
    if ($Mode -eq "test") {
        python -c "import flask" | Out-Null
    } else {
        python -c "import flask, serial" | Out-Null
    }
} catch {
    $requirementsOk = $false
}

if (-not $requirementsOk) {
    Write-Host "正在安装 Python 依赖..."
    python -m pip install --upgrade pip
    python -m pip install -r (Join-Path $InstallDir "requirements.txt")
}

$pythonExe = $pythonCmd.Source
$pythonwExe = Join-Path (Split-Path $pythonExe -Parent) "pythonw.exe"
if (Test-Path $pythonwExe) {
    $pythonExe = $pythonwExe
}

$scriptName = if ($Mode -eq "test") { "claude_hardware_companion_test.py" } else { "claude_hardware_companion.py" }
$scriptPath = Join-Path $InstallDir $scriptName
$taskRun = "`"$pythonExe`" `"$scriptPath`""

cmd /c "schtasks /Delete /TN `"$TaskName`" /F" | Out-Null 2>$null
cmd /c "schtasks /Create /TN `"$TaskName`" /SC ONLOGON /TR `"$taskRun`" /RL LIMITED /F" | Out-Null

if ($LASTEXITCODE -eq 0) {
    cmd /c "schtasks /Run /TN `"$TaskName`"" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "计划任务已创建，但立即启动失败。可以稍后手动运行，或重新登录测试。"
    }
} else {
    # 如果当前账户无权创建计划任务，则回退到当前用户启动项。
    $startupMethod = "HKCU_Run"
    $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    Set-ItemProperty -Path $runKey -Name $TaskName -Value $taskRun
    Start-Process -FilePath $pythonExe -ArgumentList "`"$scriptPath`"" | Out-Null
}

Write-Host "安装完成。"
Write-Host "服务脚本: $scriptPath"
Write-Host "运行模式: $Mode"
Write-Host "计划任务: $TaskName"
Write-Host "自启动方式: $startupMethod"
Write-Host "当前已尝试启动。可用 health 接口测试: http://127.0.0.1:8765/health"
