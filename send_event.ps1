$ErrorActionPreference = "Stop"

$body = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($body)) {
    Write-Error "stdin 为空，未收到 Claude Code hook JSON。"
    exit 1
}

try {
    $null = $body | ConvertFrom-Json
} catch {
    Write-Error "输入不是有效 JSON: $($_.Exception.Message)"
    exit 1
}

try {
    Invoke-RestMethod `
        -Uri "http://127.0.0.1:8765/event" `
        -Method Post `
        -ContentType "application/json" `
        -Body $body | Out-Null
} catch {
    Write-Error "转发失败: $($_.Exception.Message)"
    exit 1
}
