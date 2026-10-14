# ISRC LLM API 每日签到

为 [ISRC LLMAPI Gateway](https://llmapi.isrc.ac.cn) 自动完成每日签到。

本项目提供两种使用方式：

- **Python 命令行版（推荐）**：不依赖浏览器，可以手动运行，也可以使用 Windows 任务计划每天自动签到。
- **Edge / Chrome 扩展版**：复用浏览器登录状态，不需要创建系统访问令牌。

> 本工具只适用于你自己的账户。请妥善保管访问令牌，不要将它发给其他人，也不要提交到代码仓库。

## 一、Python 命令行版

### 1. 使用条件

- Windows 10 或 Windows 11
- Python 3.10 或更高版本
- 能正常访问 `https://llmapi.isrc.ac.cn`
- 网站账户已经完成登录和注册

Python 版只使用标准库，不需要运行 `pip install`。

### 2. 进入程序目录

下载并解压项目后，在 PowerShell 中进入项目目录：

```powershell
cd "你的下载路径\Auto_checkin"
```

例如，如果你把项目解压到了下载文件夹，可以是：

```powershell
cd "C:\Users\你的用户名\Downloads\Auto_checkin"
```

后续命令都需要在这个目录中运行。

### 3. 创建系统访问令牌

目标网站使用 ISRC-GitLab 登录，Python 程序不会保存你的用户名和密码，而是使用网站提供的**系统访问令牌**。

1. 登录 <https://llmapi.isrc.ac.cn>。
2. 打开 <https://llmapi.isrc.ac.cn/security>。
3. 找到“系统访问令牌”“访问令牌”或“Access Token”。
4. 创建一个令牌，名称可以填写 `Auto Checkin`。
5. 选择允许普通用户账户自助操作的权限。
6. 创建后立即复制完整令牌。令牌通常只完整显示一次。

这里需要的是“安全与访问”页面中的**系统访问令牌**，不是左侧“API 密钥”页面中用于调用模型的 API Key。

### 4. 加密保存令牌

在项目目录运行：

```powershell
python .\checkin.py --save-token
```

程序会提示：

```text
请输入要加密保存的个人访问令牌：
```

粘贴令牌并按回车。输入过程中终端不会显示文字或星号，这是正常现象。

令牌会使用 Windows DPAPI 加密后保存在 `.credential.bin` 中。该文件只能由当前 Windows 用户在当前电脑上解密。

### 5. 手动签到

运行：

```powershell
python .\checkin.py
```

也可以运行：

```powershell
.\checkin.cmd
```

可能看到以下结果：

- `签到成功`：已领取当天奖励。
- `今日已经签到`：今天已经领取，不会重复提交。
- `访问令牌无效、已过期或权限不足`：需要重新创建或保存系统访问令牌。
- `网站当前未开启每日签到功能`：签到功能被网站管理员关闭。

只查询状态、不执行签到：

```powershell
python .\checkin.py --status
```

## 二、每天自动签到

自动签到使用 Windows 自带的“任务计划程序”。Python 不会一直驻留后台，也不会循环检查时间；Windows 到达设定时间时才启动一次脚本，签到完成后脚本立即退出。

### 1. 首次创建自动任务

例如每天 `12:00` 自动签到：

```powershell
cd "你的下载路径\Auto_checkin"
python .\checkin.py --auto 12:00
```

如果此前没有保存访问令牌，程序会要求隐藏输入令牌，验证成功后自动加密保存。

看到以下内容表示配置成功：

```text
✅ 已创建每日 12:00 的自动签到计划
```

配置成功后可以关闭 PowerShell，也不需要打开 Edge。

### 2. 运行条件

- 电脑正常运行或只是锁屏：可以按时执行。
- Edge、Chrome 等浏览器完全关闭：不影响 Python 版自动签到。
- 电脑处于睡眠或完全关机状态：默认不会为了签到自动唤醒或开机。
- 错过执行时间：Windows 会在电脑和当前用户下次可以运行任务时补执行一次。
- 同一时间已有签到程序运行：不会启动第二个副本。
- 单次任务最长运行 5 分钟，之后会被 Windows 结束。

计划任务平时几乎不消耗额外性能。每天只有签到时才启动一次 Python，通常几秒后就会退出。

### 3. 修改执行时间

重新运行 `--auto` 即可覆盖原来的时间。例如改为每天 `08:30`：

```powershell
cd "你的下载路径\Auto_checkin"
python .\checkin.py --auto 08:30
```

时间必须使用 24 小时制的 `HH:MM` 格式，例如：

- `08:10`
- `12:00`
- `23:30`

### 4. 查看计划状态

```powershell
cd "你的下载路径\Auto_checkin"
python .\checkin.py --auto-status
```

### 5. 查看执行日志

自动任务的执行记录保存在项目目录下的 `checkin.log`。查看最近 20 条记录：

```powershell
cd "你的下载路径\Auto_checkin"
Get-Content .\checkin.log -Tail 20
```

`checkin.log` 每行记录一次执行时间、签到状态和服务端消息，不会记录访问令牌。

### 6. 取消自动签到

```powershell
cd "你的下载路径\Auto_checkin"
python .\checkin.py --auto-off
```

该命令只删除 Windows 计划任务，不会删除程序文件和加密令牌。

## 三、令牌管理

### 更新令牌

令牌过期、被撤销或权限发生变化后，重新运行：

```powershell
cd "你的下载路径\Auto_checkin"
python .\checkin.py --save-token
```

保存新令牌后，已有的 Windows 自动任务不需要重新创建。

### 删除本地加密令牌

```powershell
cd "你的下载路径\Auto_checkin"
python .\checkin.py --clear-token
```

删除令牌后，自动任务将无法签到，直到重新保存有效令牌。

### 不保存令牌

如果不创建 `.credential.bin`，直接运行 `python .\checkin.py` 时可以每次隐藏输入令牌。这种方式适合手动签到，不适合无人值守的自动任务。

程序也支持环境变量 `ISRC_ACCESS_TOKEN`，但环境变量通常以明文存在，不如 Windows DPAPI 加密文件安全。

不建议使用 `--token 真实令牌`，因为命令可能被终端历史记录保存。

## 四、Edge / Chrome 扩展版

如果不想创建系统访问令牌，也可以安装浏览器扩展。扩展会复用目标网站已有的登录状态。

### Edge 安装

1. 打开 `edge://extensions/`。
2. 打开“开发人员模式”。
3. 点击“加载解压缩的扩展”。
4. 选择解压后的 `Auto_checkin` 文件夹。
5. 打开 <https://llmapi.isrc.ac.cn/profile> 并登录。
6. 点击扩展图标，再点击“立即签到”进行首次验证。

### Chrome 安装

1. 打开 `chrome://extensions/`。
2. 打开“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择解压后的 `Auto_checkin` 文件夹。
5. 登录目标网站并点击扩展中的“立即签到”。

扩展默认每天本地时间 `08:10` 执行，可以在扩展弹窗中修改。

浏览器扩展只有在浏览器运行时才能工作。如果计划时间浏览器没有运行，会在之后启动浏览器时尝试补签。希望完全不依赖浏览器时，请使用 Python 自动任务。

### 扩展权限说明

| 权限 | 用途 |
| --- | --- |
| `alarms` | 安排每日任务和有限重试 |
| `scripting` | 在目标网站自身环境中调用签到接口 |
| `storage` | 保存执行时间和不含凭据的最近结果 |
| `tabs` | 复用或临时打开目标网站标签页 |
| `https://llmapi.isrc.ac.cn/*` | 将访问范围限制在目标站点 |

扩展不会申请 `cookies` 权限，不会保存账号、密码、Cookie、Access Token 或 Refresh Token。

## 五、常见问题

### 系统提示“找不到 python”

请安装 Python 3.10 或更高版本，并在安装时勾选“Add Python to PATH”。安装后重新打开 PowerShell。

也可以尝试：

```powershell
py -3 .\checkin.py
```

### 系统访问令牌和 API 密钥有什么区别？

- 系统访问令牌：用于账户管理接口，Python 签到程序需要这个令牌。
- API 密钥：用于调用大语言模型，不用于本程序签到。

### 自动任务没有执行

依次检查：

1. 运行 `python .\checkin.py --auto-status`，确认计划存在。
2. 运行 `python .\checkin.py`，确认令牌和网络正常。
3. 查看 `checkin.log` 中的错误消息。
4. 确认计划时间电脑不是关机或睡眠状态。
5. 令牌过期时，重新运行 `python .\checkin.py --save-token`。

### 可以移动项目文件夹吗？

手动签到可以，但 Windows 计划任务会记录创建任务时的程序路径。移动文件夹后，需要在新目录重新运行：

```powershell
python .\checkin.py --auto 你的执行时间
```

例如：

```powershell
python .\checkin.py --auto 12:00
```

### 如何彻底卸载？

1. 先运行 `python .\checkin.py --auto-off` 删除计划任务。
2. 如已安装浏览器扩展，在浏览器扩展管理页面中删除它。
3. 删除整个 `Auto_checkin` 文件夹。

## 六、安全说明

- Python 版不会保存用户名或密码。
- 系统访问令牌使用 Windows DPAPI 加密，绑定当前 Windows 用户和电脑。
- `.credential.bin`、`checkin.log` 和 Python 缓存文件已加入 `.gitignore`。
- 日志不会记录访问令牌。
- 签到前会先查询今日状态，避免重复提交。
- 请求结果未知时不会立即重复提交签到请求。
- 访问范围固定为 `https://llmapi.isrc.ac.cn`。

## 七、开发者测试

Python 测试：

```powershell
python -m unittest discover -s tests -p "test_checkin.py" -v
```

浏览器扩展测试：

```powershell
npm.cmd test
```
