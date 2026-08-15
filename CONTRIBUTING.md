# 贡献指南 / Contributing

感谢你愿意帮助改进 Awakening AgentTeams Demo。第一次参与开源也没有关系：从文档、错误提示或一个小测试开始，就是有效贡献。

This guide is Chinese-first. English contributors may follow the commands and the [English contribution summary](#english-contribution-summary) at the end, or open an Issue before coding.

## 先理解项目边界

本仓库展示的是一条受控的 `1 Manager + 3 Worker` AgentTeams Demo：

- Manager 是确定性的策略/契约控制面，不是 LLM planner；
- Architect 与 Coach 执行代表性 live Worker 调用；
- Reviewer 的 live 调用是 `contract_smoke`，不是正式业务评审；
- 随仓库证据来自两轮成功的 synthetic Demo，不代表真实用户成效；
- 离线核验可以独立运行，实时重跑需要兼容参考环境；
- 本地费用计算不等于远端 Provider 账单核验；
- 本仓库不包含 M5 验收、Secret、数据库或完整运行环境。

如果你的改动会改变这些边界，请不要直接提交大改动，先开 Issue 说明问题、目标、影响和验证办法。

## 适合第一次贡献的内容

- 修正文档错别字、失效的本地链接或不够清楚的说明；
- 提供一个可以稳定复现的错误案例和最小复现步骤；
- 改善 Windows PowerShell 的错误提示或跨 locale 行为；
- 为纯逻辑函数补一个小而明确的离线测试；
- 改善 Schema 示例，但不改变既有证据的历史事实；
- 补充不含 Secret、个人数据和运行时内部信息的故障排查说明。

以下改动请先讨论：Manager 路由、Agent 权限、Provider 调用、状态写入、证据口径、Security/Secret 政策、依赖升级，以及会破坏 v1.0.2 基线兼容性的修改。

## 第 1 步：先搜索 Issue

到 [Issues](https://github.com/yongjiajie337-tech/awakening-agentteams-demo/issues) 搜索是否已经有人报告相同问题。没有重复项时，再创建 Issue：

- 说明你看到了什么；
- 写出你期待的结果；
- 提供最小复现命令和非敏感输出；
- 标明 Windows、PowerShell 和 Python 版本；
- 明确是否运行过 Docker、网络或 Provider 调用。

不要把 API Key、Token、密码、`.env` 内容、个人数据、完整 Matrix 历史或原始数据库贴进 Issue。

## 第 2 步：获取代码

外部贡献者通常先在 GitHub 点击 **Fork**，再克隆自己的 Fork：

```powershell
git clone https://github.com/YOUR-USERNAME/awakening-agentteams-demo.git
Set-Location .\awakening-agentteams-demo
git remote add upstream https://github.com/yongjiajie337-tech/awakening-agentteams-demo.git
```

如果你已是仓库协作者，可以直接克隆上游仓库：

```powershell
git clone https://github.com/yongjiajie337-tech/awakening-agentteams-demo.git
Set-Location .\awakening-agentteams-demo
```

确认远程地址和当前状态：

```powershell
git remote -v
git status
```

## 第 3 步：为一个问题创建一个分支

从最新 `main` 建立短生命周期分支，不要直接在 `main` 上修改：

```powershell
git switch main
git pull --ff-only upstream main   # 使用 Fork 时
git switch -c docs/clearer-offline-guide
```

常用前缀：

- `docs/`：文档；
- `fix/`：缺陷修复；
- `test/`：测试；
- `feat/`：经 Issue 讨论后的功能；
- `chore/`：依赖、CI 或维护工作。

一个分支尽量只解决一个问题，方便复核和回退。

## 第 4 步：准备本地验证环境

离线完整核验锁定 Python 3.12 依赖。建议把虚拟环境建在仓库外，避免把环境文件混入严格 payload：

```powershell
py -3.12 -m venv ..\.venv-awakening-demo-dev
..\.venv-awakening-demo-dev\Scripts\python.exe -m pip install -r .\requirements-demo.lock
```

安装依赖可能访问 Python 包索引。安装后的 `verify_offline.ps1` 离线模式不应启动 Docker、访问网络、读取 Provider Secret 或产生模型费用。

如果暂时不能安装第三方依赖，至少运行：

```powershell
.\verify_offline.ps1 -Mode Stdlib
```

## 第 5 步：小范围修改

修改时遵循以下原则：

1. 保留真实历史。不要为了让数字“更好看”而改写两轮运行证据、费用、调用次数或失败边界。
2. 区分声明层级。`offline PASS`、参考环境 live 成功、M5 验收和生产可用不是同一件事。
3. 保持确定性边界。除非提案已讨论，不要把固定 Manager 路由描述或改造成未经约束的模型自主路由。
4. 保持 Reviewer 口径。`contract_smoke` 不能改称业务评审、事实核验或录用判断。
5. 不引入 Secret。测试使用固定 synthetic fixture 或 mock，不读取开发者机器上的代理、凭据或业务数据。
6. 保持最小改动。不要在修文档时顺便重排全部代码或更新无关依赖。

## 第 6 步：运行检查

先用外部虚拟环境中的 Python 显式重建发布清单。该命令只使用标准库；它会更新恰好三份生成文件。请检查并提交这些生成差异：

```powershell
..\.venv-awakening-demo-dev\Scripts\python.exe -I -B .\scripts\package\seal_package.py --write
..\.venv-awakening-demo-dev\Scripts\python.exe -I -B .\scripts\package\seal_package.py --check
```

`--check` 是默认模式，严格只读；如果清单过期，它只报告安全的相对文件名，不改文件。不要在仓库内创建虚拟环境来运行生成器。

然后运行与你的改动相匹配的检查。完整核验命令是：

```powershell
.\verify_offline.ps1 -Mode Full -PythonPath '..\.venv-awakening-demo-dev\Scripts\python.exe'
```

请在 Pull Request 中记录：

- 运行的准确命令；
- 退出码和 PASS/FAIL；
- skipped 项及原因；
- 未执行的检查和原因；
- 是否触发 Docker、网络、Secret 或 Provider（普通离线贡献应为“否”）。

### 关于 manifest 和哈希

`PACKAGE_MANIFEST.json`、`SHA256SUMS.txt` 和 `config/reference-source-pins.json` 责任不同。不要手工复制一个清单的内容覆盖另一个，也不要为了绕过失败删除校验项。

普通 Pull Request 也必须运行标准库生成器：

```powershell
..\.venv-awakening-demo-dev\Scripts\python.exe -I -B .\scripts\package\seal_package.py --write
```

生成器会按冻结范围重建 180 个参考源码 pin；manifest 排除自身和 `SHA256SUMS.txt`；sums 排除自身但包含 manifest。请把三份生成差异和业务改动一起提交，并在 PR 中说明生成命令。CI 会先运行只读 `--check`，再运行 Full 核验。不要手改哈希，也不要把本地残留加入清单。

如果新增或删除 Full 测试，请同步更新 `verify_offline.ps1` 的 Full 权威计数和 `verify_package.py` 的交叉校验常量；如果变更版本，请同步 `VERSION`、`pyproject.toml` 和 verifier 常量。sealer 会在这些权威值不一致时拒绝写入。

不要提交 `__pycache__`、虚拟环境、测试临时目录、日志或本地运行证据。发现残留时先清理自己的工作目录；不要把残留加入 manifest。

## 第 7 步：提交改动

先检查你实际修改了什么：

```powershell
git status --short
git diff --check
git diff
```

只暂存本次改动：

```powershell
git add path\to\changed-file
git commit -m "docs: clarify offline verification"
```

推荐提交说明格式：`类型: 简短结果`。例如：

- `docs: clarify Reviewer contract-smoke boundary`
- `fix: isolate proxy variables in offline test`
- `test: cover invalid Matrix event id`

## 第 8 步：打开 Pull Request

推送你的分支：

```powershell
git push -u origin docs/clearer-offline-guide
```

然后在 GitHub 打开 Pull Request。说明：

- 解决什么问题；
- 为什么选择这种改法；
- 哪些文件发生变化；
- 如何验证；
- 风险、限制和未覆盖内容；
- 关联的 Issue，例如 `Closes #12`。

维护者可能要求缩小范围、补测试或纠正证据口径。这是正常的评审过程，不代表你的贡献没有价值。

## Pull Request 自查清单

- [ ] 我没有提交 Secret、`.env`、Token、密码、个人数据或内部运行目录。
- [ ] 我没有把 synthetic Demo、Reviewer contract smoke 或本地费用计算夸大成更强结论。
- [ ] 改动只解决一个清晰问题，且没有混入无关格式化。
- [ ] 我更新了需要同步的文档、Schema 示例或测试。
- [ ] 我在仓库外的虚拟环境中运行了 sealer `--write` 和只读 `--check`，并提交了三份生成差异。
- [ ] 我运行并记录了适当的离线检查。
- [ ] 我说明了没有运行的检查和原因。
- [ ] `git diff --check` 没有报告空白错误。
- [ ] 我遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 安全问题不是普通 Issue

如果问题可能涉及凭据泄露、权限绕过、任意命令执行、个人数据或可被利用的安全缺陷，请不要公开细节。按照 [SECURITY.md](SECURITY.md) 的私下报告路径处理；涉及 Live、Secret、网络、Docker 或录屏的实际操作边界另见 [SECURITY_AND_SECRETS.md](SECURITY_AND_SECRETS.md)。

## 许可

除非你明确另行说明，你有权提交并希望纳入本项目的贡献，将按 [Apache License 2.0](LICENSE) 的条款提供。请只提交你有权贡献的内容，并保留适用的第三方 attribution。

## English contribution summary

1. Read [README.en.md](README.en.md) and keep the claim boundaries intact: deterministic Manager, three live Worker calls, Reviewer `contract_smoke`, two sanitized synthetic runs, no remote-billing verification, and no zero-config live reproduction claim.
2. Search [Issues](https://github.com/yongjiajie337-tech/awakening-agentteams-demo/issues) before coding. Discuss routing, permissions, Provider, state-write, evidence, security, dependency, and compatibility changes first.
3. Fork and clone the repository, create one focused branch, and never commit Secrets, `.env` files, personal data, raw Matrix history, databases, or runtime directories.
4. In the external Python 3.12 environment, run `python -I -B ./scripts/package/seal_package.py --write`, review and commit the three generated diffs, then run the default read-only `--check` mode.
5. Run `verify_offline.ps1 -Mode Stdlib` at minimum. For Full mode, use the locked environment and record the exact command, result, skips, and unperformed checks in the Pull Request.
6. Never hand-edit generated hashes or add local residue to make the seal pass. Keep the Full-test authority in `verify_offline.ps1` aligned with the verifier constant, and keep `VERSION`, `pyproject.toml`, and the verifier version aligned. CI runs the read-only sealer check before Full verification.
7. Open a focused Pull Request with the problem, rationale, changed files, verification evidence, risks, limitations, and linked Issue.

Questions that are safe to discuss publicly can be opened through [GitHub Issues](https://github.com/yongjiajie337-tech/awakening-agentteams-demo/issues). See [SUPPORT.md](SUPPORT.md) for support boundaries.

Suspected vulnerabilities or Secret exposures are not ordinary Issues or Pull Requests. Do not publish details; follow [SECURITY.md](SECURITY.md). Operational Live and Secret-handling rules remain in [SECURITY_AND_SECRETS.md](SECURITY_AND_SECRETS.md).
