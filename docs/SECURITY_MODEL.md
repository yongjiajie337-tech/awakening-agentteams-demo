# Security Model / 安全模型

[中文](#chinese) | [English Summary](#english-summary)

本文的中英文内容意图一致；如有解释冲突，以中文版为准。

The Chinese and English content is intended to be equivalent. If an interpretation conflict exists, the Chinese version controls.

---

<a id="chinese"></a>

## 中文

### 目的与适用状态

本文描述当前随包发布或准备发布的 `v1.0.x` M4 Awakening AgentTeams Demo 安全模型，包括资产、不可信输入、信任边界、固定安全不变量、已知限制和上游责任。

本文不是渗透测试报告、安全认证、生产部署指南或多租户威胁模型，也不代表未来所有版本都必须保持相同架构。

安全问题的私密报告流程见 [../SECURITY.md](../SECURITY.md)。Secret、录屏与 Live 操作规则见 [../SECURITY_AND_SECRETS.md](../SECURITY_AND_SECRETS.md)。

### 系统边界

```text
Human / fixed synthetic request
              |
              v
run_demo.ps1 / Demo orchestrator
              |
              v
Deterministic Manager control plane
(model calls = 0)
  |---------------- Architect Worker
  |---------------- Coach Worker
  `---------------- Reviewer Worker
              |
              v
Per-role Gateway and Provider call
(total Worker Provider calls <= 3 per run; retry = 0)
              |
              v
Schema-valid structured outputs
              |
              v
Deterministic summary + Matrix events + sanitized evidence

Business-state authority:
Agent / Matrix / Provider output
              |
              v
State Service admission and contract boundary
(sole authorized business-state writer;
successful apply disabled in current M4 Demo)
```

Manager 和三个 Worker 使用独立的角色、身份、Skill 和 Matrix 房间边界。

Provider 输出不是可信业务事实，也不获得业务写权限。Matrix 只用于协调与可视化，不是授权来源或业务数据库。

### 安全目标

1. **离线完整性**：离线核验不访问网络、Docker、Provider 或真实 Secret。
2. **凭据保密**：Provider Secret 和内部 Gateway Key 不进入公开仓库、证据、日志、截图或录屏。
3. **授权完整性**：Agent、Skill、tool、调用计划和业务写入必须经过固定注册表和契约边界。
4. **状态完整性**：模型输出和 Matrix 消息不能直接修改业务状态。
5. **证据完整性**：源码 pin、manifest、SHA-256 sums 和 canonical output 绑定能够发现未同步修改。
6. **Fail closed**：缺少身份、调用计划、Schema、预算、Secret ACL 或受信输入时停止，不静默放宽。
7. **声明纪律**：内部一致性、Live flow、远端账单、语义正确性和生产安全必须分别陈述。

### 需要保护的资产

| 资产 | 作用与风险 |
|---|---|
| Provider Secret | 可产生外部模型调用和费用，不得进入仓库、参数、环境变量、日志、哈希或证据 |
| Internal Gateway Key | 用于 Worker 到内部 Gateway 的认证，不是 Provider Secret，但仍属于 Secret |
| Agent 身份和角色映射 | 决定谁能使用哪个 Skill、Gateway 和状态接口 |
| Skill 与 command registry | 决定允许能力、激活方式和 deny-only 边界 |
| Schema 与 contract | 限制输入输出形状、固定字段、ID 和授权上下文 |
| Fixed synthetic package | 当前 Demo 的受信输入，通过 ID 和哈希冻结 |
| State Service authority | 唯一允许形成业务状态变更的写入边界 |
| Matrix event identities | 用于关联 request、run、Worker 和生命周期事件，不是业务授权 |
| Canonical Worker outputs | 用于公开离线复核，必须脱敏并与 Provider event hash 和 Schema 绑定 |
| Reference-source pins | 绑定兼容参考工作区必须匹配的 180 个源码、契约、Schema、Agent 和 Skill 文件 |
| Package manifest 与 SHA256SUMS | 绑定公开分发包的文件集合、大小和字节内容 |
| Release sealer 与 offline verifier | 生成或核验上述完整性清单，必须保持离线和 fail closed |

### 信任区域

| 区域 | 信任假设 |
|---|---|
| Git checkout 或解压包 | 核验前所有文件名、路径和内容均不可信；根 Git metadata 不属于 payload |
| 本地操作员 | 只被信任执行明确命令，不应被要求复制或展示 Secret |
| 兼容参考工作区 | 由操作员准备，提供 AgentTeams、Matrix、容器、身份和受保护配置；公开包不会自动部署 |
| Docker 和本地容器 | 仅在 Live 使用；Docker 管理权限通常等同高权限本机边界 |
| Matrix / Element | 消息和事件是不可信协调输入，不提供业务写授权 |
| Worker Gateway | 执行身份、调用计划、预算和 Provider 前置检查 |
| External Provider | 返回内容不可信且可能产生费用；远端账单不由本包独立核验 |
| State Service | 当前架构中唯一的业务状态写入权威 |
| GitHub Actions | 只执行封装检查、依赖安装和离线测试，不得触发 Live、Provider 或真实 Secret |

能够访问一个区域不代表自动获得另一个区域的授权。例如，能够向 Matrix 房间发送消息，不等于能够调用 Provider 或修改业务状态。

### 不可信输入

以下输入在通过验证前均视为不可信：

- Git checkout、release archive 和 PR 中的路径、文件名与内容；
- symlink、junction、reparse point、非普通文件、嵌套 `.git` 和临时目录；
- `.env`、Secret 目录、私钥、Token、Authorization Header 和带凭据 URL；
- `run_demo.ps1` 的路径、ID、阶段参数和 reference workspace；
- Matrix 消息正文、sender、room、event ID 与历史事件；
- Provider 返回文本、JSON、token usage 和错误；
- Agent 或 Worker 输出中的 ID、Skill 名称、Schema 字段和自由文本；
- proxy 环境变量、宿主网络状态和 Docker 返回；
- packaged evidence、manifest、source pins、SHA256SUMS 和 canonical outputs；
- 外部贡献的 Workflow、脚本、Schema、样例和文档。

验证失败时不得继续运行、伪造成功输出或把无效输入降级成授权输入。

### 当前 v1.0.x M4 Demo 不变量

以下不变量只适用于当前随包发布的 `v1.0.x` M4 Demo，不是未来版本的永久承诺。

#### 确定性 Manager

- Manager 是确定性策略和契约控制面；
- Manager 模型调用固定为 `0`；
- Manager 不让模型决定 Worker、Skill、tool、调用数量或重规划路径；
- 当前角色和角色到 Skill 的映射由代码与 registry 冻结；
- Manager 只汇总通过角色、Schema、ID 和关联校验的结果。

#### Worker 调用边界

- 每个 run 最多有 `3` 次 Worker Provider 调用；
- 三个调用分别对应 Architect、Coach 和 Reviewer；
- 调用可以并发，但总调用数和在途数都有硬上限；
- retry 和 hidden retry 固定为 `0`；
- 失败不得通过未记录重试、第四次调用或 Manager 模型调用掩盖；
- Reviewer 只执行 no-tool `contract_smoke`，不是正式业务评审。

#### State 写入边界

- State Service 是唯一允许形成业务状态变更的写入方；
- Agent、Worker、Matrix 和 Provider 输出不能直接写业务状态；
- 当前 M4 registry 中 `apply_authorized_change` 保持 `deny_only`；
- 当前成功 Demo 要求 `business_state_changed=false`；
- `M4_APPLY_DISABLED` 是设计边界；
- 本 Demo 不声称已展示人工批准后的成功 apply 闭环。

#### Schema 与 Contract 准入

- 每个角色只能使用 registry 允许的 Skill；
- Worker 输入输出必须满足对应 Schema 和固定字段要求；
- ID、version、event 和上下文引用必须通过格式与一致性检查；
- Reviewer 输出必须保持 `reviewer_mode=contract_smoke`、`business_evaluation=false` 和 `verified_claim_created=false`；
- Provider 输出即使是合法 JSON，也不会自动成为可信事实、批准决定或 State mutation；
- 缺少调用计划、身份、预算、Schema 或可信 package 时必须 fail closed。

### Pins、Manifest 与 Release Seal

三份清单具有不同责任：

1. `config/reference-source-pins.json`
   - 绑定 180 个 Live reference source、contract、Schema、Agent 和 Skill 文件；
   - 用于判断参考工作区是否与公开兼容实现一致；
   - 不绑定 Secret 值。

2. `PACKAGE_MANIFEST.json`
   - 绑定除自身和 `SHA256SUMS.txt` 外的 payload 文件；
   - 记录路径、byte size 和 SHA-256。

3. `SHA256SUMS.txt`
   - 绑定除自身外的所有解压文件；
   - 包含 `PACKAGE_MANIFEST.json`。

Release sealer 默认是只读 `--check`。只有显式 `--write` 才会逐文件原子替换过期生成文件。

生成前会：

- 忽略根目录 `.git`/`.GIT` checkout metadata；
- 拒绝嵌套 Git metadata、Secret 目录、`.env`、private key、Token 和临时 residue；
- 通过 `lstat` 拒绝 symlink、junction、reparse point 和非普通路径；
- 执行 UTF-8 与敏感文本边界检查；
- 交叉核验 `VERSION`、`pyproject.toml`、verifier 版本和 Full test count；
- 在内存中依次计算 pins、manifest 和 sums。

每个生成文件的替换是原子的，但三文件整体不是跨文件系统事务。中途中断可能留下部分旧、部分新文件；下一次 `--check` 和 Full verifier 会拒绝该状态。

清单和哈希证明公开文件与记录字节一致，不能单独证明模型语义、远端 Provider 计费或宿主完整性。

### 凭据模型

#### Provider Secret

兼容配置：

```text
<ReferenceWorkspace>/.secrets/demo-provider.env
AWAKENING_DEMO_PROVIDER_API_KEY=<operator-supplied-value>
```

边界：

- 真实文件只存在于独立 reference workspace；
- 仓库只提供 `config/demo-provider.env.example` 占位格式；
- 公开包不生成、迁移或复制 Provider Secret；
- Preflight 不读取 Secret 值；
- PowerShell 和 Python 都在读取前验证父目录精确为 `<ReferenceWorkspace>/.secrets`；
- `.secrets` 必须是普通、非 symlink、非 junction、非 reparse 目录；
- `.secrets` 和 Secret 文件都必须关闭 ACL 继承；
- Owner 必须是当前操作员；
- 当前操作员、SYSTEM、Administrators 必须各有一条显式 FullControl；
- 最多允许一个权限不超过 `Read + Synchronize` 的受限运行主体；
- 任何宽主体、额外创建、写入、删除、改 Owner 或改 ACL 权限都会拒绝；
- 只有其他 Provider-free 准入通过后的 Live Gateway 最终组合阶段才读取 Secret；
- Provider Secret 不进入 Git、ZIP、命令参数、进程环境变量、日志、截图、录屏、哈希或公开证据；
- ACL 检查只是本机元数据门，不证明主机、管理员、Secret 来源或云账号安全。

#### Internal Gateway Key

内部 Gateway Key 用于 Worker 到本地 Gateway 的认证。它与 Provider Secret 不同：

| 凭据 | 用途 | 边界 |
|---|---|---|
| Provider Secret | 对外 HTTPS Provider 认证并可能产生费用 | 只存在于受保护 reference workspace 文件中 |
| Internal Gateway Key | Worker 到本地 Gateway 的内部认证 | 由兼容 runtime 管理，不能作为 Provider 凭据 |

两者都必须按 Secret 处理，但不能互换。

本文档不授权任何人工或 Agent：

- inspect、echo、hash、导出或复制真实 Secret；
- 读取 Secret 来“确认是否配置正确”；
- 把 Secret 迁移到仓库、聊天、Issue、CI 或其他模块；
- 轮换、删除、覆盖或复用不在当前任务授权内的凭据；
- 将内部 Gateway Key 当成 Provider Secret。

### M4 Curl Argv 历史例外

当前 M4 Demo 唯一明确接受的凭据传递历史例外来自兼容参考环境中的旧 `Start-M4Agents.ps1`：

- 启动阶段的短时容器内 `curl` 健康探针会把内部 Gateway Key 放在容器进程参数中；
- 具有足够本机或 Docker 权限的人可能在短时间窗口看到该内部 Key；
- 该值不是 Provider Secret，但仍属于 Secret；
- Offline 模式不触发这一行为；
- 该限制只为兼容既有比赛参考环境而接受，不是生产安全设计。

该例外不授权：

- 主动提取 Key；
- 在日志、终端、截图、录屏或报告中展示 Key；
- 复制、持久化、发布、复用或跨环境传输 Key；
- 把同样做法扩大到 Provider Secret 或其他凭据；
- 在新代码中复制这一传递方式。

当前缓解措施：

- 只在隔离的本地 Demo 主机运行；
- 不向不可信本地用户授予 Docker 权限；
- 不展示容器命令行；
- 按固定流程 Stop/Restore；
- 后续生产化应改用 stdin、受保护文件描述符、header file 或不暴露参数的原生健康检查客户端。

如果报告证明 Key 被写入日志、持久文件、公开证据、宿主 argv，或暴露窗口/权限大于上述描述，应按安全漏洞私下报告。

### Offline 与 Live 模式

| 模式 | 网络 | Docker | Provider | Secret 值读取 | 状态变化 |
|---|---:|---:|---:|---:|---:|
| Sealer `--check` | 否 | 否 | 否 | 否 | 否 |
| `PackageOnly` | 否 | 否 | 否 | 否 | 否 |
| `Stdlib` | 否 | 否 | 否 | 否 | 否 |
| `Full` | 否 | 否 | 否 | 否 | 否 |
| GitHub workflow | Actions 和依赖安装可能使用网络；项目 verifier 本身不联网 | 否 | 否 | 否 | 否 |
| `run_demo.ps1 -Mode Preflight` | 可能进行公开只读传输探测 | 只读查询 | 无模型调用 | 不读取值 | 写入有限 preflight 证据 |
| 获准的 LiveStep | 是 | 是 | 最多达到固定 Worker 上限 | 在最终准入组合阶段读取 | 参考环境生命周期和 Matrix 消息会变化；M4 业务 apply 仍禁用 |

创建 Python 环境和安装依赖可能访问 package index；这发生在 verifier 执行之前，不属于 verifier 自身的联网行为。

### 主要威胁与控制

| 威胁 | 当前控制 | 剩余限制 |
|---|---|---|
| 路径穿越 | 规范化相对路径，拒绝 absolute、`..` 和非法 manifest path | 不能防御已经完全控制本机管理员的攻击者 |
| Symlink/junction escape | `lstat`，拒绝 symlink、junction、reparse 和非普通路径；Provider Secret 父目录双重检查 | 检查和后续使用之间仍依赖本机文件系统稳定 |
| 根 Git metadata 污染 payload | 只忽略根 `.git`/`.GIT` | 嵌套 Git metadata 必须 fail closed |
| Secret 被误提交 | 禁止 `.env`、`.secrets`、私钥后缀；敏感文本扫描；CI seal check | 模式扫描不能代替人工审查和凭据轮换 |
| 过期或手改哈希 | deterministic sealer `--check`；CI 在 Full 前执行 | 维护者仍需审查生成差异 |
| 不可信 Matrix 消息 | sender、room、event、ID 和上下文校验；Matrix 无 State 权限 | Matrix 管理员仍可影响消息和可用性 |
| Prompt/output injection | 固定 call plan、Schema 校验、Provider 输出无写权限 | 模型仍可能生成低质量或语义错误内容 |
| 未授权 Skill/tool | identity registry、Skill registry、command registry、Gateway admission | registry 配置错误本身仍可能形成漏洞 |
| Hidden retry 或预算绕过 | Manager `0` 调用、Worker `<=3`、retry `0`、pre-call admission | 远端账单未独立核对 |
| 未授权业务写入 | State Service 唯一 writer、M4 apply deny-only | 不证明未来 apply 路径已安全实现 |
| CI 触发 Live | Workflow 只运行 seal、依赖安装和 offline verifier | GitHub runner 和 action 属于上游信任 |
| Administrator/root/Docker daemon 被攻陷 | 明确作为高权限宿主边界 | 不在当前 Demo 防御范围 |

### 日志和公开证据

公开包只保留脱敏投影，例如：

- request/run ID；
- dispatched/completed 生命周期；
- Provider begin/succeeded/failed/end 计数；
- token 与本地费用计算；
- Matrix 阶段事件；
- canonical Worker outputs；
- output、event、lifecycle 和 package hash；
- Stop/Restore 与 listener 结论。

公开包不应包含：

- Provider Secret 或 Internal Gateway Key；
- Authorization Header；
- 完整 prompt、raw Provider response 或无关 Matrix 历史；
- Docker inspect 原文；
- `.env`、数据库、runtime state 或浏览器 profile；
- 未经授权的个人证据或 EvaluationPackage；
- 绝对用户路径、PID 或宿主敏感配置。

证据哈希支持脱敏包内部一致性和来源关联，不能独立证明：

- 模型语义正确；
- 业务批准；
- Provider 远端计费；
- 宿主不存在未披露活动；
- M5 acceptance；
- 生产就绪。

### 已知限制

当前 `v1.0.x` 安全模型具有以下限制：

- Windows-first、本地、已准备的 AgentTeams v1.1.2 兼容参考环境；
- 不提供零配置 AgentTeams/Matrix 部署；
- 不提供托管 SaaS、多租户隔离或生产 Secret Manager；
- fixed synthetic package 和两轮公开 run 不能代表任意真实输入；
- Manager 使用固定路由，不展示模型自主选择或动态重规划；
- Reviewer 是 `contract_smoke`，不是正式业务评审或事实核验；
- M4 成功 apply 保持禁用，不证明审批通过后的完整写入链路；
- 公开证据没有逐调用时间戳，不能只靠投影重新计算并发峰值；
- 远端账单没有独立核对；
- 未声明完整行覆盖、分支覆盖、渗透测试、形式化验证或安全认证；
- 敏感模式扫描是防误提交控制，不是完整 DLP；
- Provider、AgentTeams、Matrix、Docker、GitHub Actions 和操作系统属于上游依赖；
- GitHub Private Vulnerability Reporting 只有仓库页面实际显示时才可用，本项目不声明已经启用。

Seal PASS 或 Live Demo 成功不得被扩大解释为更强的安全、语义、计费或生产结论。

### 上游责任

以下组件由各自上游负责：

- AgentTeams；
- Matrix / Element；
- Docker engine、images 和 container runtime；
- Windows、PowerShell、Python 和 Git；
- Provider API、模型和远端账单；
- GitHub、GitHub Actions 和 pinned third-party actions；
- Python dependencies。

纯上游漏洞应优先报告给对应上游项目。

如果本仓库：

- 选择了不安全参数；
- 以不安全方式传递凭据；
- 错误映射身份或权限；
- 未正确验证上游返回；
- 通过配置扩大上游问题影响；
- 在公开证据或日志中泄露上游 Secret；

则属于本仓库的 integration-specific 安全范围。

### 只读核验

常用只读命令：

```powershell
python -I -B .\scripts\package\seal_package.py --check
.\verify_offline.ps1 -Mode Stdlib
```

安装锁定依赖后：

```powershell
.\verify_offline.ps1 -Mode Full `
  -PythonPath '..\.venv-awakening-demo-review\Scripts\python.exe'
```

维护者显式重建 release seal：

```powershell
python -I -B .\scripts\package\seal_package.py --write
python -I -B .\scripts\package\seal_package.py --check
```

`--write` 只用于已经审查的发布或 PR 变更。三份生成差异必须进入代码评审。

不要为了让 seal 通过而加入本地 residue、Secret 或未授权证据。

### 安全变更流程

涉及以下内容的普通 PR 应先通过 Issue 讨论；疑似漏洞应使用 [../SECURITY.md](../SECURITY.md) 的私密流程：

- 身份、角色、Skill 或 tool 权限；
- Manager 路由和调用数量；
- retry、并发、预算或 Provider admission；
- Secret 路径、ACL、读取阶段或传递方式；
- Matrix sender、room 或 event 校验；
- State 写入、审批或 apply；
- Schema、manifest、source pin 或 sealer scope；
- GitHub Actions 权限、网络、Secret 或 Live 行为；
- 已知限制或安全声明。

安全相关改动至少应：

1. 保留或收紧现有边界；
2. 添加 fail-closed 测试；
3. 使用 synthetic fixture，不读取真实 Secret；
4. 运行 sealer `--write` 和 `--check`；
5. 运行适用的 Stdlib 和 Full 检查；
6. 审查生成清单差异；
7. 更新安全模型、操作指南和 release notes；
8. 明确未运行的 Live、安全或上游检查。

### 相关文档

- [安全报告策略](../SECURITY.md)
- [Secret 与录屏操作规则](../SECURITY_AND_SECRETS.md)
- [架构与 M4 边界](ARCHITECTURE.md)
- [参考环境](REFERENCE_ENVIRONMENT.md)
- [故障排查](TROUBLESHOOTING.md)
- [证据索引与限制](../EVIDENCE.md)
- [贡献指南](../CONTRIBUTING.md)
- [支持说明](../SUPPORT.md)
- [许可证](../LICENSE)
- [Notice](../NOTICE.md)

---

<a id="english-summary"></a>

## English Summary

This document describes the current packaged `v1.0.x` M4 Demo security model. It is not a production, multi-tenant, penetration-test, or security-certification claim.

Key boundaries:

- the Manager is a deterministic control plane with `0` model calls;
- a run permits no more than `3` Worker Provider calls;
- retry and hidden retry remain `0`;
- the State Service is the sole business-state writer;
- successful apply remains disabled in the current M4 Demo;
- Provider and Matrix output is untrusted and receives no write authority;
- Schema validity is necessary but does not establish truth or authorization;
- source pins, manifests, and SHA-256 sums establish byte-level consistency, not semantic correctness;
- the release sealer is read-only by default and replaces stale generated files only with explicit `--write`;
- Offline verification does not access the network, Docker, a Provider, or a real Secret;
- Live execution requires a prepared reference workspace and explicit staged acknowledgements;
- the Provider Secret remains in the protected external file `<ReferenceWorkspace>/.secrets/demo-provider.env`;
- both `.secrets` and the Secret file must pass ordinary-path, reparse, owner, and least-privilege ACL checks before any value read;
- the only documented historical credential-passing exception is the internal Gateway Key in a short-lived container-local M4 `curl` argv health probe;
- that exception never applies to the Provider Secret and must not be copied into new code;
- the Reviewer performs `contract_smoke`, not formal business evaluation;
- remote Provider billing is not independently reconciled;
- a seal PASS or successful Demo does not establish production readiness or M5 acceptance.

Report suspected vulnerabilities through [../SECURITY.md](../SECURITY.md). Follow [../SECURITY_AND_SECRETS.md](../SECURITY_AND_SECRETS.md) for operational Secret and recording boundaries.
