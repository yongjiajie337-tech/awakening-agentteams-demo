# Security Policy / 安全政策

[中文](#chinese) | [English](#english)

两种文本意图等价；如有解释冲突，以中文版为准。欢迎私下报告翻译漂移。

The Chinese and English texts are intended to be equivalent. If an interpretation conflict exists, the Chinese version controls. Translation drift may be reported privately.

---

<a id="chinese"></a>

## 中文

本项目接受中文或英文安全报告。请不要在公开 Issue、Pull Request、截图、录屏或聊天中披露漏洞细节、Secret、个人数据或可直接使用的 PoC。

具体的 Provider API Key、Matrix/Gateway Token、数据库密码、录屏和 Live 环境操作要求见 [SECURITY_AND_SECRETS.md](SECURITY_AND_SECRETS.md)。详细威胁模型和工程边界见 [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md)。

### 支持版本

| 版本 | 支持状态 |
|---|---|
| `main` 与 `Unreleased` 开发版本 | 接受安全报告并以 best-effort 方式处理，但它们不是稳定发布版本 |
| `v1.0.3` | 仅在对应 GitHub Release 与 tag 正式发布后进入支持范围 |
| `v1.0.2` | 不可变的比赛归档基线；仍接受并分诊安全报告，但不会改写、移动或重新生成该标签，修复只会以前向版本交付 |
| `< v1.0.2` | 默认不再支持，也不承诺回补 |

“支持”表示维护者会对当前公开版本进行分级和修复评估，不表示 SLA、生产级安全认证或对每个报告提供补丁。

`v1.0.2` 的比赛证据、哈希和标签必须保持历史原样。“不可修改”不代表影响该版本的安全问题不接受报告。

### 私下报告安全问题

请不要在普通公开 Issue 或 Pull Request 中披露安全问题。

首选方式：

1. 打开本仓库 GitHub 页面中的 **Security** 页面；
2. 如果页面显示 **Report a vulnerability**，使用该入口创建私密报告；
3. 报告可以使用中文或英文，项目将尽量按照来稿语言回复。

本文件不声明 GitHub Private Vulnerability Reporting 当前已经启用。是否可用以仓库页面实时显示为准。

如果私密报告入口不可见，只能创建一个不含任何技术细节的最小公开 Issue：

- 标题：`[Security] Private contact requested`
- 正文：`I may have a security report and would like a private contact channel.`

该公开 Issue 中严禁包含：

- 漏洞原理、利用步骤、PoC、截图或录屏；
- 受影响的精确文件、代码行、提交、接口或配置；
- API Key、Token、密码、Cookie、Authorization Header 或其他 Secret；
- 真实个人数据、Matrix 消息、Provider 响应或未经脱敏的日志；
- 个人邮箱、电话号码或其他不必要的联系方式。

在私密沟通渠道建立前，请不要继续补充细节。

如果疑似真实 Secret 已经泄露，请立即停止测试、复制和传播，不要在报告中重复该值，并通过对应服务进行吊销或轮换。

### 私密报告建议包含

在不披露真实 Secret 或第三方个人数据的前提下，请尽量提供：

- 受影响版本、commit 或 tag；
- 受影响组件与前置条件；
- 最小、脱敏、可重复的复现步骤；
- 预期行为与实际行为；
- 可能造成的安全影响；
- 是否涉及网络、Docker、Matrix、Provider、Secret 或业务状态写入；
- 已进行的最小化测试；
- 建议缓解措施（如有）；
- 希望公开致谢还是保持匿名。

所有凭据必须使用占位符替代。

### 响应时间目标

| 阶段 | 目标 |
|---|---|
| 确认收到 | 7 个自然日内 |
| 初步分级 | 14 个自然日内 |
| 修复或缓解计划 | 根据严重性、复杂度、维护能力和发布风险确定 |

这些是尽力而为的目标，不是法律承诺、服务承诺或 SLA。复杂问题、上游依赖问题和协调披露可能需要更长时间。

### 适用范围

本政策主要覆盖本仓库自身实现或集成造成的安全问题，包括：

- `verify_offline.ps1`、package verifier 和 release sealer；
- `run_demo.ps1`、Preflight、LiveStep 和 Stop/Restore；
- Manager、Worker、Gateway、State、身份、Skill、Schema 与授权边界；
- manifest、SHA-256 清单、reference source pins 和脱敏证据；
- 本仓库对 AgentTeams、Matrix、Docker 与 Provider 的集成；
- Secret 暴露、命令注入、路径逃逸、越权调用；
- 未经授权的 Provider 调用或业务状态写入；
- CI 意外执行 Live、访问 Secret 或取得不必要权限。

当前随包发布的 `v1.0.x` M4 Demo 具有以下关键边界：

- Offline verifier 不得访问网络、Docker、Provider 或真实 Secret；
- Manager 模型调用为 `0`；
- 每个 fresh run 最多有 `3` 次 Worker Provider 调用；
- retry 与 hidden retry 均为 `0`；
- State Service 是唯一允许的业务状态写入方；
- 当前 M4 Demo 的成功 apply 保持禁用；
- Provider Secret 不得进入仓库、命令参数、进程环境变量、日志、哈希或公开证据；
- 结构化输出必须通过 Schema、角色和引用绑定校验。

这些数字和行为只适用于当前随包发布的 `v1.0.x` M4 竞赛 Demo，不是未来所有版本的永久架构承诺。

内部 Gateway Key 存在一个已明确披露的历史 M4 `curl` argv 兼容例外。该例外不适用于 Provider Secret，也不授权新代码复制这种传递方式。详情见 [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md)。

### 可报告问题示例

可报告问题包括：

- 命令、参数、PowerShell 或 shell 注入；
- 路径穿越、symlink、junction、reparse point、硬链接或 ADS 绕过；
- Secret、Token、密码或个人数据泄露；
- 身份、角色、Skill、工具、调用计划、预算或单次 claim 绕过；
- Provider 调用早于准入、隐藏重试或费用上限绕过；
- 不可信模型、Worker 或 Matrix 内容触发未经授权的宿主操作；
- 绕过 State Service 修改业务状态；
- verifier 接受被篡改的包、清单、pins、Schema、证据或 Worker 输出；
- 离线核验访问网络、Docker、Provider 或真实 Secret；
- CI 意外执行 Live 或暴露运行时凭据；
- 恢复流程留下非预期持久变化，却错误报告恢复成功。

严重性根据现实前提、所需权限、可达性和影响判断。

### 不在范围内

以下内容通常不属于本仓库漏洞范围：

- 仅存在于上游 AgentTeams、Matrix、Docker、操作系统、GitHub 或 Provider，且不是由本项目集成方式引入或放大的问题；
- 已经取得本机 Administrator/root、Docker daemon、当前操作员账号或物理控制权后才能实施，且没有突破本项目额外边界的问题；
- 模型幻觉、回答质量、Reviewer 业务判断、职业建议质量或 synthetic fixture 的现实代表性；
- 不涉及授权、预算或 Secret 问题的 Provider 账单争议；
- 社会工程、钓鱼、物理攻击或会影响第三方的破坏性/高负载扫描；
- 仅给出扫描器名称或通用弱点类别，没有现实可达路径和影响说明的报告；
- 文档中已经披露的限制本身，除非报告证明其影响更大、能够持久化、形成新泄露或绕过既有控制；
- 要求直接改写、移动或重新生成不可变的 `v1.0.2` 比赛标签。

影响 `v1.0.2` 安全边界的问题仍然可以报告。项目会记录影响并以前向版本修复，但不会改写历史标签。

### 协调披露

在维护者确认问题、完成初步分级并获得合理修复或缓解时间前，请保持细节私密。

维护者将尽力：

- 确认报告范围与影响；
- 请求必要的脱敏补充信息；
- 判断问题属于本项目还是上游；
- 准备修复、测试、发布说明或 GitHub Security Advisory；
- 与报告者商定合理公开时间；
- 仅在报告者同意后公开致谢。

报告者应：

- 只访问验证问题所必需的数据；
- 不持久化、下载、传播或利用第三方数据；
- 不使用真实受害者账号或真实 Secret 做 PoC；
- 不破坏数据、服务、容器或参考环境；
- 发现 Secret 时立即停止，不复制该值；
- 给项目合理的修复或缓解时间。

### 无漏洞奖金承诺

本项目目前没有漏洞赏金计划，也不承诺金钱、礼品、比赛积分、CVE、公开署名或其他奖励。

任何 advisory、CVE、公开致谢或署名都需要根据实际情况另行确认。

### 善意安全研究 Safe Harbor

如果研究者：

- 只测试自己拥有或明确获准测试的环境；
- 遵守本政策，并以善意方式尽量减少影响；
- 不访问、复制、保留或传播不属于自己的 Secret、个人数据或业务数据；
- 不造成 Provider 费用、业务状态变化、服务中断、数据破坏或第三方影响；
- 不使用社会工程、钓鱼、拒绝服务或大规模自动扫描；
- 一旦发现敏感数据或真实影响立即停止并私下报告；
- 只保留说明问题所必需的最小脱敏证据；

项目维护者将把该行为视为善意安全研究，并且在维护者能够控制的范围内，不打算因符合本政策的研究主动追究责任。

本 Safe Harbor 不是法律意见，不授权测试 GitHub、AgentTeams、Matrix、Docker、Provider、云服务或其他第三方系统，也不能约束第三方或执法机构。

### 相关文档

- [详细安全模型](docs/SECURITY_MODEL.md)
- [Secret 与录屏操作规则](SECURITY_AND_SECRETS.md)
- [架构与 M4 边界](docs/ARCHITECTURE.md)
- [参考环境](docs/REFERENCE_ENVIRONMENT.md)
- [贡献指南](CONTRIBUTING.md)
- [支持说明](SUPPORT.md)

---

<a id="english"></a>

## English

Security reports are welcome in Chinese or English. Do not disclose vulnerability details, Secrets, personal data, or directly usable proof-of-concept material in a public Issue, Pull Request, screenshot, recording, or chat.

For operational Provider API key, Matrix/Gateway token, database-password, recording, and Live-environment rules, see [SECURITY_AND_SECRETS.md](SECURITY_AND_SECRETS.md). For the detailed threat model and engineering boundaries, see [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).

### Supported Versions

| Version | Support status |
|---|---|
| `main` and `Unreleased` work | Reports are accepted and handled on a best-effort basis, but this is not a stable release |
| `v1.0.3` | Supported only after the corresponding GitHub Release and tag are published |
| `v1.0.2` | Immutable competition baseline; reports are accepted and triaged, but the tag will not be rewritten, moved, or regenerated. Fixes are delivered forward |
| `< v1.0.2` | Not supported by default; no routine backports |

Supported means that maintainers will triage and assess remediation for the current published release. It does not imply an SLA, production certification, or a patch for every report.

The `v1.0.2` competition evidence, hashes, and tag remain historically unchanged. Immutable does not mean out of scope.

### Reporting a Vulnerability Privately

Do not disclose security details in an ordinary public Issue or Pull Request.

Preferred route:

1. Open the repository’s GitHub **Security** page.
2. If **Report a vulnerability** is available, use it to submit a private report.
3. Reports may be written in Chinese or English; the project will try to reply in the submitted language.

This policy does not claim that GitHub Private Vulnerability Reporting is currently enabled. Availability is determined by the live repository page.

If the private reporting entry is unavailable, create only a minimal public Issue containing no technical detail:

- Title: `[Security] Private contact requested`
- Body: `I may have a security report and would like a private contact channel.`

Do not include:

- vulnerability mechanics, exploitation steps, PoCs, screenshots, or recordings;
- exact affected files, lines, commits, endpoints, or configurations;
- API keys, tokens, passwords, cookies, Authorization headers, or other Secrets;
- real personal data, Matrix messages, Provider responses, or unsanitized logs;
- personal email addresses, phone numbers, or unnecessary contact details.

Do not add details until a private channel exists.

If a real Secret may have been exposed, stop testing, copying, and distribution immediately. Do not repeat the value in the report; revoke or rotate it through the relevant service.

### What to Include

Without exposing real Secrets or third-party personal data, include where possible:

- affected version, commit, or tag;
- affected component and prerequisites;
- minimal, sanitized, reproducible steps;
- expected and actual behavior;
- potential security impact;
- whether network, Docker, Matrix, Provider, Secret, or business-state writes are involved;
- the least invasive testing performed;
- suggested mitigation, if available;
- whether you prefer public credit or anonymity.

Replace all credentials with placeholders.

### Response Targets

| Stage | Target |
|---|---|
| Acknowledgement | Within 7 calendar days |
| Initial triage | Within 14 calendar days |
| Remediation or mitigation plan | Based on severity, complexity, maintainer capacity, and release risk |

These are best-effort targets, not legal commitments, service commitments, or an SLA. Complex findings, upstream dependencies, and coordinated disclosure may require more time.

### Scope

This policy primarily covers security issues caused by this repository’s own implementation or integration, including:

- `verify_offline.ps1`, the package verifier, and the release sealer;
- `run_demo.ps1`, Preflight, LiveStep, and Stop/Restore;
- Manager, Worker, Gateway, State, identity, Skill, Schema, and authorization boundaries;
- manifests, SHA-256 lists, reference source pins, and sanitized evidence;
- repository-specific AgentTeams, Matrix, Docker, and Provider integration;
- Secret exposure, injection, path escape, or authorization bypass;
- unauthorized Provider calls or business-state writes;
- CI unexpectedly executing Live behavior, accessing Secrets, or receiving unnecessary permissions.

For the current packaged `v1.0.x` M4 competition Demo only:

- the offline verifier does not access the network, Docker, a Provider, or a real Secret;
- Manager model calls are `0`;
- each fresh run permits at most `3` Worker Provider calls;
- retry and hidden retry are `0`;
- the State Service is the sole authorized business-state writer;
- successful apply remains disabled in the current M4 Demo;
- the Provider Secret does not enter the repository, command arguments, process environment, logs, hashes, or public evidence;
- structured output must pass Schema, role, and reference-binding validation.

These values are not permanent architecture commitments for future versions.

An explicitly documented historical M4 `curl` argv compatibility exception exists for the internal Gateway Key. It does not apply to the Provider Secret and does not authorize new code to copy that pattern. See [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).

### Reportable Examples

Examples include:

- command, argument, PowerShell, or shell injection;
- path traversal or symlink, junction, reparse-point, hard-link, or ADS bypass;
- Secret, token, password, or personal-data exposure;
- identity, role, Skill, tool, call-plan, budget, or single-use-claim bypass;
- Provider calls before admission, hidden retries, or cost-cap bypass;
- untrusted model, Worker, or Matrix content causing unauthorized host actions;
- business-state mutation bypassing the State Service;
- the verifier accepting tampered packages, seals, Schemas, evidence, or Worker outputs;
- offline verification accessing the network, Docker, a Provider, or a real Secret;
- CI unexpectedly executing Live behavior or exposing runtime credentials;
- recovery reporting success while leaving unexpected persistent changes.

Severity is based on realistic prerequisites, privilege, reachability, and impact.

### Out of Scope

Generally out of scope:

- issues solely in upstream AgentTeams, Matrix, Docker, operating-system, GitHub, or Provider components, unless this repository’s integration introduces or amplifies the issue;
- attacks requiring prior full control of local Administrator/root, the Docker daemon, the current operator account, or physical access, where this repository crosses no additional boundary;
- model hallucinations, answer quality, Reviewer business judgment, career-advice quality, or synthetic-fixture representativeness;
- Provider billing disputes without authorization, budget, or Secret impact;
- social engineering, phishing, physical attacks, or disruptive/high-volume scanning;
- scanner-only reports without a realistic reachable path and impact;
- a documented limitation by itself, unless the report shows broader exposure, persistence, new leakage, or a control bypass;
- requests to rewrite, move, or regenerate the immutable `v1.0.2` competition tag.

Security issues affecting `v1.0.2` may still be reported and fixed forward without rewriting that historical tag.

### Coordinated Disclosure

Keep details private until maintainers have acknowledged the issue, completed initial triage, and had a reasonable opportunity to fix or mitigate it.

Maintainers will make a best-effort attempt to:

- confirm scope and impact;
- request necessary sanitized information;
- determine whether the issue belongs here or upstream;
- prepare a fix, tests, release notes, or a GitHub Security Advisory;
- agree on a reasonable publication date;
- provide public credit only with the reporter’s consent.

Reporters should:

- access only data required to verify the issue;
- avoid retaining, downloading, distributing, or exploiting third-party data;
- avoid real victim accounts and real Secrets in PoCs;
- avoid damaging data, services, containers, or reference environments;
- stop and avoid copying a value when a Secret is encountered;
- allow a reasonable remediation window.

### No Bounty

This project currently has no bug-bounty program and does not promise payment, gifts, competition credit, a CVE, public attribution, or other rewards.

Any advisory, CVE, acknowledgement, or attribution is subject to separate confirmation.

### Safe Harbor

Good-faith research must:

- be limited to environments owned by the researcher or explicitly authorized for testing;
- follow this policy and minimize impact;
- avoid accessing, copying, retaining, or distributing Secrets, personal data, or business data that do not belong to the researcher;
- avoid Provider cost, business-state changes, service disruption, data destruction, or third-party impact;
- avoid social engineering, phishing, denial of service, and broad automated scanning;
- stop and report privately upon encountering sensitive data or real impact;
- retain only the minimum sanitized evidence needed to explain the finding.

Maintainers will treat such activity as good-faith security research and, within the scope controlled by the maintainers, do not intend to initiate legal action based on research that complies with this policy.

This Safe Harbor is not legal advice, does not authorize testing GitHub, AgentTeams, Matrix, Docker, a Provider, cloud services, or other third-party systems, and cannot bind third parties or law-enforcement authorities.

### Related Documents

- [Detailed security model](docs/SECURITY_MODEL.md)
- [Operational Secret and recording guidance](SECURITY_AND_SECRETS.md)
- [Architecture and M4 boundaries](docs/ARCHITECTURE.md)
- [Reference environment](docs/REFERENCE_ENVIRONMENT.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
