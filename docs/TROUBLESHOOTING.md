# 故障排查

## 先判断你在做哪一层

| 症状 | 所属层 | 首要动作 |
|---|---|---|
| Python/依赖/哈希失败 | 离线核验 | 不启动 Docker，修复本地评审环境或重新解压 |
| `REFERENCE_WORKSPACE_*` | 真实复现 | 这是外部动作前的安全停止；检查兼容参考工作区路径或只做离线核验 |
| Docker/Matrix/Element 失败 | 真实复现 | 停止 Live，恢复服务后重新 Preflight |
| Worker/Provider/summary 不完整 | 真实复现 | 保存当前 run，按同一 ID 查证，禁止盲目重发 |

## 离线核验

### PowerShell 阻止脚本运行

优先只为当前进程临时允许本地脚本，不修改整机长期策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\verify_offline.ps1 -Mode Full
```

组织管理设备应遵循组织策略；也可以人工审阅脚本后由允许的 PowerShell 主机执行。

### 找不到 Python 3.12

```powershell
py --list
py -3.12 --version
```

入口优先尝试 `py -3.12`，然后检查 PATH 中的 `python.exe`。安装 Python 3.12，或显式指定具体解释器文件：

```powershell
.\verify_offline.ps1 -Mode Full -PythonPath 'C:\path\to\python.exe'
```

- `OFFLINE_VERIFY_PYTHON_3_12_REQUIRED`：没有找到可工作的 Python 3.12；
- `OFFLINE_VERIFY_PYTHON_PATH_INVALID`：`-PythonPath` 不存在、不是普通文件或是 reparse point；
- `OFFLINE_VERIFY_PYTHON_VERSION_UNSUPPORTED`：指定文件可以启动，但不是 Python 3.12。

不要把 `py -3.12` 整段填入 `-PythonPath`；该参数只接受一个解释器文件路径。

### 依赖缺失、版本不匹配或导入失败

确认正在使用包专用虚拟环境：

```powershell
..\.venv-awakening-demo-review\Scripts\python.exe -m pip install -r .\requirements-demo.lock
.\verify_offline.ps1 -Mode Full -PythonPath '..\.venv-awakening-demo-review\Scripts\python.exe'
```

默认完整核验会先检查 `requirements-demo.lock` 中所有锁定版本，再运行 `tests/unit/demo` 与 `tests/unit/m4` 的完整测试。依赖门失败时会输出不含 Secret 的具体 `package`、`expected`、`actual` 或 lock 行类别，随后使用稳定错误码停止；不会先输出 payload 或总 `PASS`。

- `OFFLINE_VERIFY_DEPENDENCY_LOCK_INVALID`：输出 `line` 与固定 `category`，但不会回显原始行；重新解压原包，不要直接改 lock；
- `OFFLINE_VERIFY_DEPENDENCY_MISSING`：输出缺少的包与精确期望版本；在包外虚拟环境中按 lock 安装；
- `OFFLINE_VERIFY_DEPENDENCY_VERSION_MISMATCH`：输出期望版本与当前版本；新建干净的包外 Python 3.12 环境，不要混用全局依赖；
- `OFFLINE_VERIFY_DEPENDENCY_IMPORT_FAILED`：metadata 版本正确但模块无法导入；重新创建包外虚拟环境。

### Full、Stdlib 与 PackageOnly

不安装 Python 第三方依赖时，仍可运行标准库测试 allowlist：

```powershell
.\verify_offline.ps1 -Mode Stdlib -PythonPath 'C:\path\to\python.exe'
```

标准库档包含 8 项 Worker shell 契约，其中 7 项为静态源码检查，只有 1 项动态负向检查实际需要 Git for Windows Bash；未安装时该 1 项会被明确跳过。

只检查包结构、payload、证据、pin、哈希和敏感文件时使用：

```powershell
.\verify_offline.ps1 -Mode PackageOnly -PythonPath 'C:\path\to\python.exe'
```

旧 `-SkipUnitTests` 是 `PackageOnly` 的兼容别名。它不等于完整核验；也不能与显式的 `Full` 或 `Stdlib` 同时使用，否则以 `OFFLINE_VERIFY_MODE_CONFLICT` 停止。实际发现的测试数量以脚本输出为准。

### 手动运行 Python 后出现临时残留

三档官方入口都会把解压目录当作封存的评审 payload 核验。`verify_offline.ps1` 自身会用 Python 的 `-I -B` 参数，并在核验期间临时设置 `PYTHONDONTWRITEBYTECODE=1`，因此官方入口不会生成 `__pycache__`。但直接执行 `python -m unittest ...`、手动 import 包内模块或某些编辑器/插件的 Python 分析命令，可能在包目录内生成 `__pycache__`，使后续核验以 `PACKAGE_TRANSIENT_RESIDUE_FOUND` 停止。

当前 package verifier 会在检测到这类残留时输出下面两个稳定标签：

- `PACKAGE_TRANSIENT_RESIDUE_FOUND=type=python-bytecode;path=<relative-directory>`：包目录中出现了手动工具产生、但不属于封存 payload 的 `__pycache__`、`.pyc` 或 `.pyo`；只报告安全相对目录，不输出缓存文件内容；
- `PACKAGE_TRANSIENT_RESIDUE_RECOVERY=REEXTRACT_ORIGINAL_ZIP`：保留原目录用于查看需要的信息，从原 ZIP 重新解压到另一个全新目录，并在任何手动 Python 命令之前先运行官方核验入口。

不要为了让核验变绿而修改 `PACKAGE_MANIFEST.json`、`SHA256SUMS.txt` 或 `config/reference-source-pins.json`，也不要在包目录内创建虚拟环境。虚拟环境应始终放在包目录外：

```powershell
py -3.12 -m venv ..\.venv-awakening-demo-review
```

如需在另一个工作副本中手动执行 Python，可用 `-B` 禁止写入字节码，或为当前 PowerShell 进程设置对应环境变量：

```powershell
C:\path\to\python.exe -B -m unittest discover tests\unit\demo
$env:PYTHONDONTWRITEBYTECODE = '1'
C:\path\to\python.exe -m unittest discover tests\unit\demo
```

这些手动命令仅用于自定义检查，不能替代从全新解压目录运行的 `verify_offline.ps1`。

### Git Bash 与 WSL Bash

Worker shell 的动态负向契约测试只会选择 Git for Windows 的 `Git\bin\bash.exe` 或 `Git\usr\bin\bash.exe`。`C:\Windows\System32\bash.exe` 是 WSL 启动器，不会被误当成 Git Bash。若 Git Bash 未安装，对应动态用例会明确跳过；其余静态安全契约仍会执行。

### 参考工作区路径被拒绝

- `REFERENCE_WORKSPACE_EXPLICIT_PATH_REQUIRED`：未提供路径；
- `REFERENCE_WORKSPACE_PATH_INVALID`：路径文本无法按本机文件系统规则安全解析；
- `REFERENCE_WORKSPACE_NOT_FOUND`：路径不存在；
- `REFERENCE_WORKSPACE_NOT_DIRECTORY`：路径指向普通文件；
- `REFERENCE_WORKSPACE_REPARSE_POINT_DENIED`：根路径是 junction/symlink 等 reparse point；
- `REFERENCE_WORKSPACE_METADATA_UNAVAILABLE`：路径存在，但无法安全读取其必要文件元数据；
- `PACKAGE_ROOT_IS_NOT_A_LIVE_REFERENCE_WORKSPACE`：误把评审包本身当作已准备的真实环境。

这些检查发生在 reference runner、Docker、网络和 Secret 动作之前。请提供普通、独立、已准备好的兼容参考工作区；不要用链接绕过边界。

### SHA-256 不匹配

不要直接更新 `SHA256SUMS.txt` 来掩盖差异。按以下顺序：

1. 重新从原 ZIP 解压到新目录；
2. 确认杀毒/同步软件没有重写文件；
3. 比较 `PACKAGE_MANIFEST.json`；
4. 若原 ZIP 也不匹配，停止使用并联系包提供者。

### Secret 扫描误报

文档会出现 `API Key`、`password` 等安全术语，这不等于含有真实值。判断应结合允许的占位符与内容格式。但以下情况必须立即停止：真实 `.env`、私钥头、Authorization Bearer 值、看似真实的长 token 或明文账号密码。

## 真实参考环境

### 重启后 Docker 没有启动

人工启动 Docker Desktop，等待状态显示引擎 ready，然后先运行 `Preflight`。不要直接重复 `Live`。

### Docker daemon 无法连接

检查：

- Docker Desktop 是否使用 Linux containers；
- 当前 Windows 用户是否有预期 Docker 权限；
- Docker context 是否被切换；
- 引擎是否仍在启动中。

不要通过删除 Docker 数据或重建所有容器来快速处理，这可能破坏参考环境。

### Element 显示 “Connectivity to the server has been lost”

这表示浏览器与 Matrix homeserver 的连接中断，不等于 Agent run 自动失败或成功。

1. 不要重复发送同一任务；
2. 恢复 homeserver/Element 连接；
3. 按 `demo_request_id`/`demo_run_id` 检查是否已有消息；
4. 结合 lifecycle/Gateway 证据判定 run；
5. 只有形成新的原因和干净前置条件后，才创建新的 fresh run。

### Manager 看不到 Worker 的完整长回复

这是当前点对点房间设计的预期表现。到对应 `Worker:` 房间查看完整结构化结果；Manager 房间用于阶段事件和最终汇总。不要通过 Invite 把所有角色临时改成群聊，这会改变已验证拓扑。

### Worker 房间有回复，但 Manager summary 未完成

可能原因包括：

- 还有另一个 Worker 未完成；
- 结果 schema 校验失败；
- Gateway/Relay/Matrix 回执未完成；
- 浏览器连接丢失但后台仍在处理；
- run ID 混入旧消息。

保存该 run 的现状，按同一 ID 检查 exact 3 个 Worker 状态，不要机械重发。

### `403 TOOL_NOT_ALLOWED`

表示 Manager/Worker 路径尝试了当前 allowlist 未允许的工具或调用方式。不要反复加费用额度；费用额度不能解决工具 ACL。应检查固定 Demo 路径是否使用了允许的 Matrix/Relay/Gateway 操作，或改回已验证的 synthetic Demo runner。

### Provider 调用失败

1. 不要打印或检查 API Key 内容；
2. 使用非秘密状态检查确认受保护配置存在；
3. 检查网络、Provider endpoint、模型可用性与账号配额；
4. 检查是否已有调用/费用记录，避免隐藏重试；
5. 保存当前 run 为失败证据；
6. 有新根因和针对性修复后再创建新的 fresh run。

### Demo 结束后仍有监听器或进程

停止创建新 run。执行参考 runner 的 Stop/Restore 检查，使用角色/端口/受限容器字段确认，而不是仅依赖 PID；Windows PID 可能被无关进程复用。

## 何时停止并求助

出现以下任一情况时停止：

- 任何 Secret 出现在终端、日志、截图或录屏；
- 不清楚当前 run 是否已经触发 Provider；
- Docker 状态与运行前基线不一致；
- 参考工作区同时被另一个写入对话修改；
- 包哈希无法从原 ZIP 复现；
- 需要删除数据库、Docker volume、历史证据或重置工作区才能继续；
- 真实费用或外部数据授权边界不清楚。

停止后保留现状、记录 run ID 和可见错误，不要清理证据或机械重试。
