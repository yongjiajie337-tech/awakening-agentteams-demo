# Windows 快速开始

本文把评审分为两条互不混淆的路径：

1. **离线核验（推荐）**：不启动 Docker、不访问网络、不读取 Secret、不产生模型费用；
2. **真实流程复现（可选）**：只支持已经准备好的 AgentTeams v1.1.2 兼容参考环境。

## A. 离线核验

### 1. 解压并进入包目录

建议解压到不含权限限制的普通目录，例如：

```powershell
Set-Location 'D:\review\awakening-agentteams-demo-v1.0.2'
```

不要从压缩包预览窗口直接运行脚本。

### 2. 检查 PowerShell 与 Python

```powershell
$PSVersionTable.PSVersion
py -3.12 --version
```

支持 Windows PowerShell 5.1 或 PowerShell 7；Python 必须是 3.12。入口会优先尝试 `py -3.12`，其次检查 PATH 中的 `python.exe`。`-PythonPath` 必须指向具体的 Python 3.12 解释器文件，不能写成目录，也不能在参数中附带 `-3.12`。

### 3. 创建隔离虚拟环境

```powershell
py -3.12 -m venv ..\.venv-awakening-demo-review
..\.venv-awakening-demo-review\Scripts\python.exe -m pip install -r .\requirements-demo.lock
```

虚拟环境必须放在代码包目录之外，否则会改变受哈希保护的文件集合。依赖安装需要访问 Python 包索引；安装结束后的核验本身不需要网络。若评委使用组织内镜像，可按组织策略设置 pip index。

### 4. 运行默认完整核验入口

```powershell
.\verify_offline.ps1 -Mode Full -PythonPath '..\.venv-awakening-demo-review\Scripts\python.exe'
```

期望最终输出包含明确的 `PASS`，并且进程退出码为 `0`：

```powershell
$LASTEXITCODE
```

入口也支持自动寻找 `py -3.12` 或 PATH 中的 Python 3.12：

```powershell
.\verify_offline.ps1 -Mode Full
```

### 5. 离线核验检查什么

- 必需文件、目录和示例是否存在；
- `SHA256SUMS.txt` 与包内文件是否匹配；
- 证据投影的 run/request ID、计数、哈希和结果摘要是否自洽；
- 锁定依赖的版本是否完整匹配；
- `tests/unit/demo` 与 `tests/unit/m4` 的完整离线单元/合约测试是否通过；
- 包内是否出现禁止分发的 `.env`、运行时 secret、Token、数据库与常见明文密钥模式；
- 核验期间是否保持 Docker、网络、Provider、Secret 访问为关闭状态。

核验不会证明远端 Provider 服务当前可用，也不会证明本机已经拥有可运行的 AgentTeams 实例。

### 6. 三档核验模式

| 模式 | 命令 | 检查范围 | Python 第三方依赖 |
|---|---|---|---|
| `Full`（默认、推荐） | `.\verify_offline.ps1 -Mode Full ...` | package verifier、精确依赖门、完整 Demo/M4 测试 | 必须与 `requirements-demo.lock` 完全一致 |
| `Stdlib` | `.\verify_offline.ps1 -Mode Stdlib ...` | package verifier，以及入口中显式列出的标准库测试 allowlist | 不需要 |
| `PackageOnly` | `.\verify_offline.ps1 -Mode PackageOnly ...` | payload、证据、pin、哈希、敏感文件等 package verifier 检查；不运行单元测试 | 不需要 |

标准库档可执行：

```powershell
.\verify_offline.ps1 -Mode Stdlib -PythonPath 'C:\path\to\python.exe'
```

`Stdlib` 中有一组 8 项 Worker shell 契约：其中 7 项是纯静态源码检查，只有 1 项动态负向检查会实际调用 Git for Windows Bash；未安装 Git Bash 时，该 1 项会明确标记为 skipped，其余标准库测试仍会执行。WSL 的 `bash.exe` 不会被当成 Git Bash。

仅需检查包完整性时可执行：

```powershell
.\verify_offline.ps1 -Mode PackageOnly -PythonPath 'C:\path\to\python.exe'
```

旧参数 `-SkipUnitTests` 继续兼容，并严格等价于 `-Mode PackageOnly`；它不能替代推荐的 `Full`。同时显式提供 `-SkipUnitTests` 与非 `PackageOnly` 模式会以 `OFFLINE_VERIFY_MODE_CONFLICT` 停止。

完整模式会先完成 Python 3.12 与依赖预检，之后才允许运行 package verifier，因此缺包、错版本或导入失败时不会先输出 payload 或总 `PASS`。每档实际发现并执行的测试数会由 `OFFLINE_UNIT_TEST_DISCOVERED` 和 `OFFLINE_UNIT_TEST_COUNT` 明确输出。

## B. 阅读样例

固定 synthetic 输入在：

```text
examples/input/
```

对应的人类可读安全输出在：

```text
examples/output/
```

它们用于理解流程和测试结构，不会被当作真实求职者数据或 M5 验收输入。

## C. 真实 1 Manager + 3 Worker 复现（可选）

### 先决条件

真实模式不是裸机安装器。必须已经有：

- Windows 宿主与正常运行的 Docker Desktop（Linux containers）；
- AgentTeams v1.1.2 兼容实例；
- Matrix homeserver 与可登录的 Element Web；
- 已配置的 1 Manager + 3 Worker 身份、房间和服务账号；
- 参考工作区中已存在、受保护且不在本包中的 Provider/Gateway 配置；
- 已理解可能产生的 Provider 调用与费用；
- 对历史 M4 内部 Gateway Key 探针限制的明确接受。

完整清单见 [docs/REFERENCE_ENVIRONMENT.md](docs/REFERENCE_ENVIRONMENT.md)。

### 1. 执行预运行准入

重启电脑后，Docker Desktop 可能没有自动启动。先由人工确认 Docker Desktop 已运行，再执行：

```powershell
$demoRunId = [guid]::NewGuid()
.\run_demo.ps1 -Mode Preflight `
  -ReferenceWorkspace 'D:\path\to\compatible-reference-workspace' `
  -DemoRunId $demoRunId `
  -IUnderstandThisUsesDockerAndNetwork `
  -IUnderstandThisChangesReferenceState
```

`Preflight` 不调用模型、不读取 Secret 值，但会做公网传输探测、只读查询 Docker，并在参考工作区创建 fresh 证据目录。缺少环境或配置时应 fail closed；它不会为了“帮你补齐”而复制秘密文件。保存它输出的 `DemoRunId`，后续所有步骤必须使用同一个值。

### 2. 打开可视化界面

在浏览器打开参考环境的 Element 地址（参考实例常用本机地址形如 `http://127.0.0.1:<port>/`），登录演示管理员账号。

进入：

- `Manager: default` 房间观察调度与汇总；
- 三个 `Worker:` 房间观察各角色的结构化回复。

Agent 之间采用 Manager 与 Worker 的独立房间通信，不要求四个 Agent 都加入同一个群聊。

### 3. 按阶段触发真实流程

在明确接受网络、费用和安全边界后：

```powershell
.\run_demo.ps1 -Mode PrintRunbook
```

按打印出的顺序，用同一 `$demoRunId` 执行 `StartInfrastructure`、`AwaitHumanRequest`、`StartLiveGateway`、`RunChain`、`StopRestore`。每次 `Live` 都必须带 `-ReferenceWorkspace`、`-DemoRunId`、`-LiveStep`、`-IUnderstandThisUsesDockerAndNetwork`、`-IUnderstandThisChangesReferenceState` 和 `-IUnderstandThisMayReadProtectedSecret`；`RunChain` 再加 `-IUnderstandThisMayCallProvider`。主路径会读取内部运行时/Gateway 凭据、Matrix Token 或 Provider Secret；`StopRestore` 的故障恢复分支也可能读取内部 Gateway 凭据。任何值都不得输出或复制。

关键人工时序：先启动 `AwaitHumanRequest` 并保持命令运行；只有看到 `DEMO_HUMAN_ACTION` 后，才在 Element 中原样发送脚本打印的唯一消息一次。不要提前发送、编辑、回复或重复发送。无论成功或失败，最终都执行 `StopRestore`。

`<YOUR_EXISTING_HUMAN_MATRIX_USER_ID>` 必须替换成已经加入 Manager direct room 的现有人工 Matrix MXID，格式形如 `@name:matrix-m4.local:8080`；不要原样照抄尖括号占位符，也不要在文档或录屏中暴露该账号密码。

运行过程中不要并行启动第二个 Demo，也不要让另一个对话修改同一参考工作区。

公开入口不暴露内部历史恢复 attempt。若主路径失败，先保留证据并执行 `StopRestore`；不要猜测或调用底层 `Resume*` 动作，只有维护者在精确匹配的历史状态与专用执行卡下才可使用它们。

### 4. 录屏建议

录屏文件不放入代码包，单独提交。建议按以下顺序录制：

1. 代码包 README 与离线核验 PASS；
2. Element 中 Manager 房间；
3. 触发新的 fresh Demo request；
4. 展示 3 次 worker-dispatched 与 3 次 worker-completed；
5. 依次打开三个 Worker 房间展示不同角色结果；
6. 回到 Manager 房间展示 summary-completed；
7. 展示安全汇总：3/3 Worker、Provider 3/3/0、Manager 调用 0、retry 0；
8. 停止/恢复完成后说明没有残留 Demo 监听器。

录屏前隐藏通知、账号菜单、开发者工具、终端环境变量和任何可能包含秘密的窗口。

## 常见失败

- `OFFLINE_VERIFY_PYTHON_3_12_REQUIRED`：先运行 `py -3.12 --version`；必要时安装 Python 3.12，或用 `-PythonPath` 指向具体解释器文件。
- `OFFLINE_VERIFY_PYTHON_PATH_INVALID` / `OFFLINE_VERIFY_PYTHON_VERSION_UNSUPPORTED`：路径不是普通解释器文件，或解释器不是 Python 3.12。
- `OFFLINE_VERIFY_DEPENDENCY_LOCK_INVALID`：锁文件行不是安全的 `包名==精确版本` 格式；重新解压原包，不要手改 lock。
- `OFFLINE_VERIFY_DEPENDENCY_MISSING`：输出会给出缺少的 `package`、`expected` 与 `actual=MISSING`；在包外虚拟环境中重新安装 lock。
- `OFFLINE_VERIFY_DEPENDENCY_VERSION_MISMATCH`：输出会给出 `package`、`expected` 与 `actual`；新建干净的包外虚拟环境后重新安装 lock。
- `OFFLINE_VERIFY_DEPENDENCY_IMPORT_FAILED`：版本 metadata 匹配但模块无法导入；不要复用损坏的全局环境，重新创建包外虚拟环境。
- `OFFLINE_VERIFY_MODE_CONFLICT`：不能同时使用 `-SkipUnitTests` 与 `Full`/`Stdlib`；改用一个明确的 `-Mode`。
- `REFERENCE_WORKSPACE_EXPLICIT_PATH_REQUIRED` / `REFERENCE_WORKSPACE_NOT_FOUND`：没有提供参考工作区，或路径不存在；这是启动 Docker/网络前的安全停止。
- Docker 连接失败：人工启动 Docker Desktop，等待引擎完全就绪，再重做 `Preflight`。
- Element 显示连接丢失：不要重复发送任务；先恢复 homeserver/Element 连接并确认是否已有 run。

更多信息见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。
