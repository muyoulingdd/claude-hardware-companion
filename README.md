# Claude Hardware Companion

Windows 常驻服务，用于接收 Claude Code 官方 hooks 事件，并把事件转发给本地硬件设备或本机测试日志。

项目提供两种运行模式：

- `hardware`：正式版。自动发现 USB CDC 设备，并通过串口发送事件。
- `test`：测试版。不需要 USB 设备，只把事件写入本地日志文件。

## 功能特性

- Flask 本地 HTTP 服务，默认监听 `http://127.0.0.1:8765`
- 兼容 Claude Code hooks
- 自动归一化 Claude 事件
- 内置去重、节流、状态机
- 正式版支持 USB 串口自动发现与断线重连
- Windows 开机登录自动启动
- 权限不足时自动回退到 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`

## 事件映射

- `PermissionRequest` -> `PERMISSION_WAIT`
- `Notification(permission_prompt)` -> `PERMISSION_WAIT`
- `TaskCompleted` -> `TASK_DONE`
- `Stop` -> `ROUND_STOP`

## 状态机规则

- 1.2 秒内同类事件只处理一次
- 已处于 `waiting_permission` 状态时，再收到 `PERMISSION_WAIT` 直接忽略
- `TASK_DONE` 优先级高于 `ROUND_STOP`
- `TASK_DONE` 后 3 秒内收到 `ROUND_STOP` 直接忽略
- `idle` 状态下收到 `ROUND_STOP` 直接忽略

## 仓库结构

```text
claude_hardware_companion.py
claude_hardware_companion_test.py
install_startup.ps1
send_event.ps1
settings.json
requirements.txt
main.ino
README.md
```

## 运行环境

- Windows 10 或 Windows 11
- Python 3.10+
- PowerShell 5.1 或 PowerShell 7
- Claude Code

## Python 依赖

本项目使用标准的 `requirements.txt`：

```text
Flask>=3.0,<4.0
pyserial>=3.5,<4.0
```

安装命令：

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 快速开始

### 1. 克隆仓库

```powershell
git clone https://github.com/your-name/claude-hardware-companion.git
cd claude-hardware-companion
```

### 2. 安装 Python 依赖

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. 安装测试版常驻服务

测试版不需要 USB 设备，适合先验证 Claude hooks 是否能正常触发：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_startup.ps1 -TaskName ClaudeHardwareCompanionTest -Mode test
```

### 4. 安装正式版常驻服务

正式版会尝试自动发现 USB CDC 设备，并通过串口发送事件：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_startup.ps1 -TaskName ClaudeHardwareCompanion -Mode hardware
```

### 5. 配置 Claude Code hooks

把仓库里的 `settings.json` 中的 `hooks` 部分合并到 Claude Code 配置文件。

Windows 常见路径：

```text
C:\Users\你的用户名\.claude\settings.json
```

示例 hooks 配置：

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:/ClaudeHardware/send_event.ps1\""
          }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "permission_prompt",
        "hooks": [
          {
            "type": "command",
            "command": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:/ClaudeHardware/send_event.ps1\""
          }
        ]
      }
    ],
    "TaskCompleted": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:/ClaudeHardware/send_event.ps1\""
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:/ClaudeHardware/send_event.ps1\""
          }
        ]
      }
    ]
  }
}
```

## 自动安装脚本做了什么

运行 `install_startup.ps1` 后，脚本会：

1. 把项目文件复制到 `C:\ClaudeHardware\`
2. 安装 `requirements.txt` 中的依赖
3. 优先尝试通过 Task Scheduler 注册登录自启动
4. 如果 Task Scheduler 权限不足，则自动回退到 `HKCU\...\Run`
5. 立即启动服务

## 手动运行

### 手动启动测试版

```powershell
cd C:\ClaudeHardware
python .\claude_hardware_companion_test.py
```

### 手动启动正式版

```powershell
cd C:\ClaudeHardware
python .\claude_hardware_companion.py
```

## 验证服务是否正常

### 查看健康状态

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

测试版正常时会看到类似：

```json
{
  "mode": "test",
  "status": "ok",
  "state": "idle",
  "local_action": "file_log_only"
}
```

正式版正常时会看到类似：

```json
{
  "status": "ok",
  "state": "idle",
  "serial_connected": false,
  "serial_port": null
}
```

### 手动发送测试事件

下面这些命令可以直接复制粘贴：

```powershell
'{"hook_event_name":"PermissionRequest"}' | powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:/ClaudeHardware/send_event.ps1"
'{"hook_event_name":"TaskCompleted"}' | powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:/ClaudeHardware/send_event.ps1"
'{"hook_event_name":"Stop"}' | powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:/ClaudeHardware/send_event.ps1"
```

### 查看测试版日志

```powershell
Get-Content C:\ClaudeHardware\test_last_event.txt
Get-Content C:\ClaudeHardware\test_events.log
```

## 正式版硬件配置

默认配置：

```python
BAUDRATE = 115200
PRODUCT_STRING = "ClaudeHookDevice"
USB_VID = None
USB_PID = None
```

如果你的设备使用不同的 USB Product String，可以修改：

```python
PRODUCT_STRING = "YourDeviceName"
```

如果你想按 VID/PID 精确匹配：

```python
PRODUCT_STRING = None
USB_VID = 0x239A
USB_PID = 0x80F4
```

修改后，重新启动服务即可。

## 自动启动方式

脚本会优先尝试：

- Windows Task Scheduler

如果系统权限不足，会自动回退到：

- `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`

你可以用下面命令检查当前用户启动项：

```powershell
Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
```

## 卸载

### 删除测试版启动项

```powershell
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "ClaudeHardwareCompanionTest"
```

### 删除正式版启动项

```powershell
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "ClaudeHardwareCompanion"
```

### 删除计划任务

```powershell
schtasks /Delete /TN "ClaudeHardwareCompanion" /F
schtasks /Delete /TN "ClaudeHardwareCompanionTest" /F
```

### 删除安装目录

```powershell
Remove-Item -Recurse -Force C:\ClaudeHardware
```

## 故障排查

### Claude 显示 hook 执行失败

确认 `settings.json` 中命令路径是：

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:/ClaudeHardware/send_event.ps1"
```

不要省略引号，也不要改成没有分隔符的 Windows 路径字符串。

### `/health` 无法访问

先确认进程是否存在：

```powershell
Get-Process python,pythonw -ErrorAction SilentlyContinue
```

再确认端口是否监听：

```powershell
netstat -ano | findstr 8765
```

### 正式版没有找到 USB 设备

查看健康状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

如果 `serial_connected` 是 `false`，说明服务本身在线，但没有匹配到设备。请检查：

- 设备是否已插入
- 波特率是否正确
- `PRODUCT_STRING` 是否匹配
- 是否需要改为 `VID/PID` 匹配

## 固件示例

`main.ino` 是一个 Arduino 风格示例，会按串口收到的命令切换 LED 状态。串口协议每次发送一行文本：

```text
PERMISSION_WAIT
TASK_DONE
ROUND_STOP
```

## 许可证

你可以在仓库中补充自己的 `LICENSE` 文件，例如 MIT License。
