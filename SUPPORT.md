# 支持与问题反馈 / Support

本项目是开源竞赛 Demo 与复现材料，不提供商业 SLA、托管 AgentTeams 实例、Provider 账号、费用报销或生产环境运维。维护者会尽力帮助定位可复现的仓库问题，但不能保证固定回复时间。

## 开始前先看这些内容

1. [README.md](README.md)：项目能力、真实证据和复现边界；
2. [QUICKSTART_WINDOWS.md](QUICKSTART_WINDOWS.md)：三种离线核验模式与实时分阶段入口；
3. [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)：常见错误码与恢复方法；
4. [docs/REFERENCE_ENVIRONMENT.md](docs/REFERENCE_ENVIRONMENT.md)：为什么 live 流程需要兼容参考环境；
5. [SECURITY.md](SECURITY.md)：漏洞私下报告、支持版本与协调披露；
6. [SECURITY_AND_SECRETS.md](SECURITY_AND_SECRETS.md)：Secret、网络、Docker、录屏与费用操作边界；
7. [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md)：威胁模型、工程不变量与已知限制；
8. [EVIDENCE.md](EVIDENCE.md)：两轮运行证据、哈希和限制。

## 可以公开提交的内容

如果问题不包含安全或隐私信息，请到 [GitHub Issues](https://github.com/yongjiajie337-tech/awakening-agentteams-demo/issues) 提交：

- 离线核验稳定失败；
- 文档错误或本地链接失效；
- Schema、样例或 evidence binding 的可复现不一致；
- Windows、PowerShell、Python 或 Git Bash 兼容性问题；
- 一个范围明确的改进建议。

为了更快定位，请附上：

- 使用的 Git commit 或 tag；
- Windows、PowerShell、Python 和 Git Bash 版本；
- 运行的准确命令；
- 退出码和完整的**脱敏后**错误码/输出；
- 预期结果与实际结果；
- 最小复现步骤；
- 是否触发 Docker、网络、Secret 或 Provider。

请先搜索已有 Issue，避免重复报告。

## 不要公开提交的内容

以下内容不要放在 Issue、Pull Request、Discussion、截图或日志附件中：

- API Key、Gateway Key、密码、Matrix Token、Cookie 或 Authorization header；
- `.env` 内容、Secret 文件路径与值、数据库转储、Docker volume 内容；
- 求职者简历、个人项目证据、姓名、联系方式或其他个人信息；
- 未脱敏的 Matrix 历史、完整 Provider request/response 或内部运行目录；
- 可立即利用的漏洞步骤或仍有效的凭据泄露证据。

如果怀疑问题涉及 Secret、权限绕过、任意命令执行、个人数据或其他安全风险，请先保留最小、脱敏证据，不要公开细节，并严格按照 [SECURITY.md](SECURITY.md) 的私下报告路径处理。该政策说明 GitHub 私密报告入口可用时的首选方式，以及入口不可见时不得包含技术细节的最小公开联系请求。平台级滥用可直接使用 GitHub 的举报功能。

## 支持范围

| 问题 | 支持程度 |
|---|---|
| 仓库自带离线 verifier 失败 | 优先处理可稳定复现的问题 |
| 文档、Schema、样例、测试问题 | 欢迎 Issue 或 Pull Request |
| GitHub checkout、PowerShell、Python 兼容性 | 在文档支持的版本范围内尽力协助 |
| AgentTeams v1.1.2 兼容参考环境 | 只协助本仓库边界内的复现问题，不代建完整环境 |
| Docker Desktop、Matrix、Element、Provider 平台本身 | 请同时查阅对应上游官方支持渠道 |
| Provider 账号、额度、远端账单 | 不属于本仓库支持范围 |
| 生产部署、可用性、安全认证 | 本 Demo 不提供此类承诺 |
| M5 验收或内部主线材料 | 不通过本公开仓库处理 |

## 看到什么才算有效结论

- `PackageOnly PASS`：包结构、payload、证据、pin、哈希和敏感文件检查通过，但没有运行 unittest；
- `Stdlib PASS`：额外运行无需第三方依赖的测试；
- `Full PASS`：运行锁定依赖下的随包聚焦测试；
- live reference run 成功：只表示兼容参考环境中再次完成该固定 `1+3` 流程；
- 上述任何一项都不自动等于生产可用、远端账单已核验、M5 验收或真实用户成效。

## English summary

For non-sensitive, reproducible repository problems, open a [GitHub Issue](https://github.com/yongjiajie337-tech/awakening-agentteams-demo/issues) with the commit/tag, environment versions, exact command, exit code, sanitized output, expected/actual result, minimal reproduction, and whether Docker, network, Secrets, or a Provider were involved.

Never publish credentials, `.env` contents, tokens, personal evidence, databases, raw Matrix history, complete Provider traffic, or exploitable vulnerability details. Follow [SECURITY.md](SECURITY.md) for the authoritative private-reporting path, supported versions, and coordinated-disclosure guidance; use [SECURITY_AND_SECRETS.md](SECURITY_AND_SECRETS.md) for operational Live and Secret boundaries. This project provides best-effort open-source support, not a response-time SLA, hosted environment, Provider account/billing support, or production operations.
