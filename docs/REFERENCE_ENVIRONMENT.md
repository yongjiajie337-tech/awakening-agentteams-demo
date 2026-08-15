# AgentTeams v1.1.2 兼容参考环境

## 定位

本包的真实运行入口是“参考环境复现器”，不是“裸机安装器”。它假定 1 Manager + 3 Worker 的 AgentTeams/Matrix 环境已经配置并曾成功运行。Manager 是固定角色—Skill 映射的确定性策略/契约控制面，不是模型自主路由器。

离线评审不需要本文所述环境；只有希望再次触发真实流程时才需要。

## 已验证的参考形态

- Windows 宿主；
- Docker Desktop 使用 Linux containers；
- AgentTeams v1.1.2 兼容运行形态；
- Matrix homeserver + Element Web；
- 1 个 Manager 与 3 个 Worker：
  - `role_project_architect`
  - `execution_evidence_coach`
  - `independent_quality_reviewer`
- 独立 Manager/Worker Matrix 房间；
- 宿主 Relay、State 服务、Gateway 与 PostgreSQL 依赖；
- Python 3.12；
- 受保护的本地 Provider 配置，且不在本代码包中。

“AgentTeams v1.1.2 兼容”表示配置、容器入口、房间/身份和脚本行为与成功环境一致；不表示所有未来版本或其他发行形态自动兼容。

真实入口会读取包内 `config/reference-source-pins.json`，对 180 个公开运行源码、脚本、Schema、契约、Agent 与 Skill 文件逐项核对 SHA-256。Secret、运行态 `.env`、数据库、Docker volume 和有意脱敏的 `controller.env.example` 不进入该 pin 清单，也不会被复制到包中。

`infra/agentteams/m4/runtime-images.lock.json` 固定参考环境的 Manager/Worker 镜像 digest。文件内的 `evidence` 值 `artifacts/m4/M4_RUNTIME_IMAGES_ATTEMPT_1.md` 是原 M4 受控工作区中的**历史来源指针**；该历史 artifact 按公开包脱敏边界不随包分发，因此在本 ZIP 内会悬空。它不是运行依赖，也不是离线核验必需文件；包内可执行准入依据是 lock 文件中的 image reference/digest 与 `reference-source-pins.json`，不能把这个历史指针冒充为公开链接。

## 重启电脑后的检查顺序

电脑重启后，优先检查：

1. Docker Desktop 是否已启动且引擎 ready；
2. 参考工作区是否仍在原路径；
3. Matrix/Element 本机地址是否可访问；
4. 管理员演示账号能否登录；
5. 四个角色房间是否存在；
6. 参考工作区的非秘密配置与身份绑定是否完整；
7. 是否没有另一个 Demo/M5 写入对话同时运行；
8. 是否理解真实 Provider 调用和费用。

不要通过把 `.env` 或 runtime secret 复制到评审包的方式“修复”缺失配置。

## Preflight

在包根目录执行：

```powershell
$demoRunId = [guid]::NewGuid()
.\run_demo.ps1 -Mode Preflight `
  -ReferenceWorkspace 'D:\path\to\compatible-reference-workspace' `
  -DemoRunId $demoRunId `
  -IUnderstandThisUsesDockerAndNetwork `
  -IUnderstandThisChangesReferenceState
```

Preflight 应确认：

- 路径是明确的参考工作区，而不是本代码包目录；
- 必需 Demo/M4 脚本和配置存在；
- Docker 与目标容器/网络可被安全查询；
- Element/Matrix 参考端点可用；
- 角色与绑定满足 1+3 拓扑；
- 没有明显并发 Demo；
- Secret 文件只在参考环境受保护位置存在，不输出其内容；
- 真实调用前预算/授权条件明确。

Preflight 不调用模型、不读取 Secret 值，但它会做公网传输探测、只读 Docker 检查，并在参考工作区写入 fresh 准入证据，因此不是“完全只读”。

冻结 shell 中的固定 JSON `probe_body` 是后续 **live 凭据 preflight/probe** 的 contract fixture，不由公开 `-Mode Preflight` 执行，因此不改变上一段“Preflight 不读取 Secret”的边界。它只验证现有内部 Gateway 凭据能到达 `403 / CALL_PLAN_UNAVAILABLE` 的 fail-closed 边界，并要求 Provider 调用数为 `0`。它不是项目方输入的业务任务，也不应到达 Provider；shell 保持不改是为了维持参考源码 pin。

任何必需条件不满足时应 fail closed，不应自动创建账号、覆盖配置或降低权限。

## Live

```powershell
.\run_demo.ps1 -Mode PrintRunbook
```

真实运行必须使用 Preflight 的同一 `$demoRunId`，并按 `PrintRunbook` 给出的 `LiveStep` 顺序逐步执行。`AwaitHumanRequest` 启动并打印 `DEMO_HUMAN_ACTION` 后，才允许在 Element 中发送那一条 exact 消息；不得提前或重复发送。所有公开 `LiveStep` 都需要显式确认受保护 Secret 读取；`RunChain` 还需要显式确认 Provider 调用。主路径读取的分别是内部运行时/Gateway 凭据、Matrix Token 或 Provider Secret，`StopRestore` 的故障恢复分支也可能读取内部 Gateway 凭据；值都不得离开受控进程。

`PrintRunbook` 中的 `<YOUR_EXISTING_HUMAN_MATRIX_USER_ID>` 是占位符，必须替换成已在 Manager direct room 中的现有人工 Matrix MXID（例如格式 `@name:matrix-m4.local:8080`），不能照抄尖括号文本；无需也不得在命令中提供密码。

公开 wrapper 只开放已验证主路径，不开放底层 `ResumeAdmissionCheck`/`ResumeInfrastructure`。这些恢复分支绑定特定历史 journal、哈希和 attempt，不是通用重试功能；普通复现失败后应保留证据并执行 `StopRestore`。

Live 运行可能产生以下外部变化：

- Docker 容器进入运行态，结束后再恢复；
- Matrix 中新增一个 fresh Demo request 及阶段消息；
- 三个 Worker 各发起一次真实 Provider 调用；其中 Reviewer 是 no-tool contract smoke，不是正式业务评审；
- 本地新增 run 目录、Gateway/Matrix/lifecycle 日志；
- 产生少量真实模型费用。

请记录新的 `demo_request_id`、`demo_run_id` 与 outer run ID；不要用旧消息作为新 run 的证据。

## Element 中如何观察

登录演示管理员账号后：

1. 打开 `Manager: default`，观察 request accepted、三路 dispatch、三路 completed 和 summary；
2. 打开 `Worker: role_project_architect`，观察架构角色回复；
3. 打开 `Worker: execution_evidence_coach`，观察执行/证据建议；
4. 打开 `Worker: independent_quality_reviewer`，观察 no-tool contract-smoke 结果，并确认其中 `business_evaluation=false`；
5. 回到 Manager 房间确认最终 summary 与同一 run ID。

房间分离是正常设计，不需要点击 “Invite to this room” 把四个 Agent 拉入同一群聊。

## 费用与 Provider

历史成功 Demo 的本地计算费用分别约为 ¥0.007176 和 ¥0.005740，但这不是未来运行的承诺，也不是远端账单核验。

单 run 的 Worker Provider 调用总数和同时在途数都受 `3` 的硬上限约束；历史证据的 `max_inflight=3` 是实际峰值观测，不是未来并发承诺。

真实运行前应：

- 确认当前 Provider、模型、单价与账号配额；
- 使用明确的 Demo 预算；
- 禁止隐藏 retry；
- 运行后查看安全 usage receipt；
- 不在终端或录屏中展示 API Key。

## 已知安全限制

历史 M4 `Start-M4Agents.ps1` 的短时容器内 `curl` 探针会把内部 Gateway Key 放入进程参数。仅在隔离演示机且明确接受该限制时运行。详见 [../SECURITY_AND_SECRETS.md](../SECURITY_AND_SECRETS.md)。

## 结束与恢复

完成后必须让参考 runner 执行 Stop/Restore，并确认：

- 目标角色恢复到运行前状态；
- Demo listener 为 0；
- 没有重复运行中的 Demo 进程；
- 新证据目录已冻结，不再被后台进程写入；
- 不删除或改写旧成功/失败历史；
- 不把本次 Demo 的新日志误并入 M5 验收。

如果连接中断或结果不完整，先保存现状并判定当前 run，禁止无变化机械重发。
