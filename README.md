# Claude Hardware Companion

Windows 常驻服务，用于接收 Claude Code 官方 hooks 事件，并把事件转发给本地硬件设备或本机测试日志。

项目当前采用“协议层解耦”的设计：

- 电脑端只负责抓取 Claude 事件、归一化 signal、做去重和状态机、再传输出去
- 硬件端决定收到 signal 后要做什么效果
- 默认示例固件仍保持原来的 LED 行为不变

项目提供两种运行模式：

- `hardware`：正式版。自动发现 USB CDC 设备，并通过串口发送事件。
- `test`：测试版。不需要 USB 设备，只把事件写入本地日志文件。

## 功能特性

- Flask 本地 HTTP 服务，默认监听 `http://127.0.0.1:8765`
- 兼容 Claude Code hooks
- 自动归一化 Claude 事件为统一 signal
- 内置去重、节流、状态机
- 正式版支持 USB 串口自动发现与断线重连
- Windows 开机登录自动启动
- 权限不足时自动回退到 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- 默认兼容旧版串口协议，方便直接复用原始示例固件

## 抓取哪些 Claude 询问行为

除了任务完成与停止，本项目现在会抓取更完整的“询问类行为”：

- `PermissionRequest`
  用于 Claude 请求权限时
- `Notification(permission_prompt)`
  用于权限提示，包括是否允许执行 Bash / 进程
- `Notification(elicitation_dialog)`
  用于 Claude 主动向用户发起提问
- `Notification(idle_prompt)`
  用于 Claude 等待用户输入时的提示
- `PreToolUse(AskUserQuestion)`
  用于用户提问类工具调用前

其中“是否执行进程 / Bash”的询问，会被单独识别成专门的 signal。

## 统一 Signal 协议

电脑端内部会先归一化成这些 signal：

- `CLAUDE_PERMISSION_REQUEST`
- `CLAUDE_PROCESS_CONFIRM_REQUEST`
- `CLAUDE_USER_QUESTION`
- `CLAUDE_IDLE_INPUT`
- `CLAUDE_TASK_DONE`
- `CLAUDE_ROUND_STOP`

这些 signal 的中文含义分别是：

- `CLAUDE_PERMISSION_REQUEST`
  Claude 正在请求权限确认，例如读写文件、调用某个受控工具
- `CLAUDE_PROCESS_CONFIRM_REQUEST`
  Claude 正在请求是否允许执行 Bash / 进程 / 命令行操作
- `CLAUDE_USER_QUESTION`
  Claude 正在主动向用户发起提问，等待用户回答
- `CLAUDE_IDLE_INPUT`
  Claude 当前空闲，正在等待用户继续输入
- `CLAUDE_TASK_DONE`
  Claude 当前任务已经完成
- `CLAUDE_ROUND_STOP`
  Claude 当前这一轮交互已经停止或结束

默认串口输出协议为 `legacy` 兼容模式：

- `CLAUDE_PERMISSION_REQUEST` -> `PERMISSION_WAIT`
- `CLAUDE_PROCESS_CONFIRM_REQUEST` -> `PERMISSION_WAIT`
- `CLAUDE_TASK_DONE` -> `TASK_DONE`
- `CLAUDE_ROUND_STOP` -> `ROUND_STOP`
- `CLAUDE_USER_QUESTION` -> `CLAUDE_USER_QUESTION`
- `CLAUDE_IDLE_INPUT` -> `CLAUDE_IDLE_INPUT`

这样做的好处是：

- 原来的示例固件不需要改就能继续工作
- 新硬件固件可以直接处理更细粒度的 signal

## 状态机规则

- 1.2 秒内同类事件只处理一次
- 已处于 `waiting_permission` 状态时，再收到 `PERMISSION_WAIT` 直接忽略
- `TASK_DONE` 优先级高于 `ROUND_STOP`
- `TASK_DONE` 后 3 秒内收到 `ROUND_STOP` 直接忽略
- `idle` 状态下收到 `ROUND_STOP` 直接忽略

额外补充：

- `CLAUDE_USER_QUESTION` 在 `waiting_user_question` 状态下会去重
- `CLAUDE_IDLE_INPUT` 在 `waiting_user_input` 状态下会去重

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
      },
      {
        "matcher": "elicitation_dialog",
        "hooks": [
          {
            "type": "command",
            "command": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:/ClaudeHardware/send_event.ps1\""
          }
        ]
      },
      {
        "matcher": "idle_prompt",
        "hooks": [
          {
            "type": "command",
            "command": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:/ClaudeHardware/send_event.ps1\""
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "AskUserQuestion",
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

## 协议层与硬件解耦

正式版脚本里有一个可配置项：

```python
SERIAL_PROTOCOL = "legacy"
```

默认值 `legacy` 表示：

- 继续发旧版单行文本协议
- 原版 `main.ino` 可以直接使用

如果你要做完全自定义的硬件端协议，可以改成：

```python
SERIAL_PROTOCOL = "json"
```

此时串口会发送一行 JSON，例如：

```json
{"signal":"CLAUDE_PROCESS_CONFIRM_REQUEST","legacy_event":"PERMISSION_WAIT","source":"PermissionRequest","tool_name":"Bash","notification_type":null,"title":null,"message":null}
```

这样硬件端就可以完全根据 `signal` 自定义行为，而不是被固定的 LED 逻辑绑定。

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

### 查看正式版日志

正式版默认会写文件日志：

```powershell
Get-Content C:\ClaudeHardware\companion.log
Get-Content C:\ClaudeHardware\companion.log -Tail 30
```

日志会记录：

- 收到并处理的 signal
- 被状态机过滤的原因
- 串口未连接或写入失败
- 当前状态变化

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

测试版日志现在保存的是 JSON 行，每一行都会包含：

- `signal`
- `legacy_event`
- `source`
- `tool_name`
- `notification_type`
- `title`
- `message`

这样你可以更方便地区分：

- 普通权限询问
- Bash / 进程执行确认
- Claude 主动提问
- 等待用户输入

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

`main.ino` 是一个 Arduino 风格示例，会按串口收到的命令切换 LED 状态。它保持原始行为不变，只处理这些旧协议命令：

```text
PERMISSION_WAIT
TASK_DONE
ROUND_STOP
```

如果收到新的 signal，例如：

```text
CLAUDE_USER_QUESTION
CLAUDE_IDLE_INPUT
```

当前示例固件会自然忽略。你可以在自己的硬件固件里自行添加处理逻辑。

## 许可证

你可以在仓库中补充自己的 `LICENSE` 文件，例如 MIT License。

## 参考文档

Claude Code hooks 官方文档：

- https://docs.anthropic.com/en/docs/claude-code/hooks
