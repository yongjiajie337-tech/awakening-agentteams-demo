# 运行证据索引

## 证据结论

本包提供两次真实 1 Manager + 3 Worker Demo 成功运行的 **脱敏安全投影**。两次运行均完成三路 Worker 并发、三份结构化结果、确定性 Manager 汇总和 Matrix 可视化阶段事件。Architect/Coach 是 representative live call；Reviewer 是 no-tool `contract_smoke_live_call`，不是正式业务评审。

证据支持的声明是：

> 在已准备的 AgentTeams v1.1.2 兼容参考环境中，固定 synthetic job package 曾两次完成 1 Manager + 3 Worker 的真实可视化流程。

证据不支持以下扩大声明：

- 任意新机器可以零配置运行；
- M5 已因此验收；
- 远端 Provider 账单已经独立核验；
- 所有 AgentTeams 版本、模型或网络条件都兼容；
- 样例内容代表真实求职者或真实生产数据。
- M4 已执行 HumanDecision 批准后的成功 apply、V2/V3 或完整业务写入闭环；
- 公开安全投影含有足以让第三方独立时序重放 Provider 并发的完整时间字段。

## Run A

| 字段 | 值 |
|---|---|
| Outer run ID | `eccfaadd-dd07-42aa-ae41-68ea7c97d65b` |
| Core demo run ID | `55dfc571-4cae-4483-b98e-8570bf5f9760` |
| Demo request ID | `9d9ea216-66e2-405c-a63f-64cf0570bb3a` |
| Worker 成功 | `3/3` |
| Provider begin/succeeded/failed | `3/3/0` |
| Manager 模型调用 | `0` |
| Provider retry | `0` |
| 单 run Worker 调用/在途硬上限 | `3 / 3` |
| 实际峰值在途 `max_inflight` | `3` |
| Tokens | input `28,498` / output `1,844` |
| 本地计算费用 | `¥0.007176` |
| Matrix 事件 | exact `8` |
| Result SHA-256 | `07bf221e158712759f1293982399bcb8334493bdb9fe33bdb1c997f29ac8d52c` |
| Matrix SHA-256 | `8de491e1971c0aa6513e44642ded1edebff0de0c7435f4f7abd631e237facd10` |
| Lifecycle SHA-256 | `4535c6df2de15c93728d4764f6d375365d1d79a3bc43467bc75eb7a0e3389215` |

## Run B（最终录屏对应运行）

| 字段 | 值 |
|---|---|
| Outer run ID | `3f481126-507b-41b9-8be6-01f4a1b17f2a` |
| Core demo run ID | `2bccbb6c-7bce-4211-b1a3-2b1e105de0fb` |
| Demo request ID | `135540e8-ddd7-465a-a402-58f88139ee25` |
| Worker 成功 | `3/3` |
| Provider begin/succeeded/failed/end | `3/3/0/3` |
| Manager 模型调用 | `0` |
| Provider retry / hidden retry | `0/0` |
| 单 run Worker 调用/在途硬上限 | `3 / 3` |
| 实际峰值在途 `max_inflight` | `3` |
| Tokens | input `21,549` / output `1,785` / total `23,334` |
| 本地计算费用 | `5,740 microCNY = ¥0.005740` |
| Matrix 事件 | exact `8` |
| Lifecycle 事件 | exact `14` |
| Result SHA-256 | `99637380c83ef33d4c081eebfc88a37136527fe55829485654c55f794d3b9f91` |
| Gateway stdout SHA-256 | `118cab238ba22604a78fbee83568cc2467b3ea4438a442e8615aae37d0434dc1` |
| Gateway stderr SHA-256 | empty-file SHA-256 |
| Matrix SHA-256 | `142c8b8225d0eda8535e816bb8c38ae16165be82ce651ff2ec419762da0d6f93` |
| Lifecycle SHA-256 | `7666a33f18b003e997c443098b883884ce5b4f909bef0fc7e4de1e78495b5262` |
| Run stdout SHA-256 | `472a549e3a4f304a8ee843b2f9acf91e2705ebfdce0389a7140e3cc878ba393a` |
| Stop/Restore | exact `8` roles restored; Demo listener `0` |

Run B 的独立安全审计还确认：8 个目标容器的安全字段与基线匹配，全部为 `exited`、退出码 `0`、restart count `0`，四个暴露端口均为 `0`，核验时没有仍在运行的 Demo 进程。曾记录的宿主 PID 后来被无关进程复用，因此 PID 本身没有放入公开证据；“没有残留”以角色、监听器和受限安全投影判断。

两张表中的 `max_inflight=3` 来自 live 进程内 `ObservedProvider` 计数器：每次调用开始前增加受锁保护的在途数，结束时在 `finally` 中减少，同时记录峰值。该数值与冻结结果及证据哈希绑定；但 `provider-events.jsonl` 是安全结果投影，不含逐调用 `started_at/ended_at`，所以本包不声称评委能只用投影独立重新计算并发区间。

## Matrix 阶段事件

每个成功 run 的 exact 8 条阶段事件为：

```text
request-accepted    x1
worker-dispatched   x3
worker-completed    x3
summary-completed   x1
```

每条消息使用同一 `demo_request_id` 和 `demo_run_id` 关联，Worker 目标分别为：

- `role_project_architect`
- `execution_evidence_coach`
- `independent_quality_reviewer`

Element 中 Manager 房间看到阶段事件和最终汇总；每个 Worker 房间看到 Manager 给该角色的任务，以及该 Worker 的结构化回复。Worker 回复不一定全部复制回 Manager 的直接聊天正文；Manager 使用固定角色映射、契约校验和编排结果生成 summary，不进行模型自主路由。这是当前点对点房间拓扑的预期行为。

## 证据目录

```text
evidence/
  run-a/
    outputs/    # Run A 的 3 份 canonical 真实 Worker 输出
  run-b/
    outputs/    # Run B 的 3 份 canonical 真实 Worker 输出（最终录屏对应）
```

每个 run 除安全摘要、Provider/Matrix/lifecycle 投影和哈希外，还包含三份 canonical Worker 输出：`role_project_architect.json`、`execution_evidence_coach.json`、`independent_quality_reviewer.json`。两次 run 合计 6 份。最终文件列表和原始文件字节的 SHA-256 以包根目录的 `PACKAGE_MANIFEST.json` 和 `SHA256SUMS.txt` 为准。

## 为什么是“脱敏安全投影”

完整运行目录可能包含宿主路径、进程 ID、容器细节、账号信息、运行时 secret、数据库日志或与本次比赛声明无关的历史。因此 3A 方案只纳入：

- 固定 synthetic 输入与安全结果摘要；
- 从冻结 `result.json` 中对应 `calls[].output` 提取的 6 份 canonical 结构化 Worker 输出；
- run/request ID；
- Worker/Provider/Matrix/lifecycle 计数；
- 与结果和事件绑定的 SHA-256；
- 由 Gateway marker 派生的 Provider 结果、用量与并发安全投影；
- Stop/Restore 的安全结论。

以下内容有意排除：

- `.env`、API Key、Gateway Key、Token、密码；
- 原始 Provider 传输 envelope、prompt、完整消息正文；
- 完整容器 inspect、完整数据库、完整 Matrix 历史；
- 宿主 PID、绝对用户路径、机器基线；
- M5 主线日志、失败历史和其他对话内容；
- 录屏视频本体。

## 6 份 canonical Worker 输出如何复核

每份 `outputs/*.json` 都是对应成功 run 的真实 Worker 结构化结果，不是 `examples/output/` 中的 synthetic 格式样例。生成时采用 UTF-8、JSON 键排序、紧凑分隔符并在文件末尾保留 LF。离线核验会：

1. 解析该文件并以 `ensure_ascii=false`、`allow_nan=false`、键排序、紧凑分隔符重新 canonicalize；
2. 对 canonical JSON 内容计算 SHA-256（语义哈希不含展示文件末尾的 LF）；
3. 与同一 run 的 `provider-events.jsonl` 对应角色 `output_sha256` 精确比对；
4. 按角色映射到 `analyze_role_gap`、`coach_task_submission`、`review_evidence_against_rubric` 的 output schema，执行 Draft 2020-12 与 UUID format 校验。

因此，评委可以在不取得被排除的原始 Provider envelope 或完整运行目录的情况下，独立重算这 6 份结构化结果的语义哈希并验证契约。Reviewer 文件自身明确标记 `reviewer_mode=contract_smoke`、`business_evaluation=false`、`verified_claim_created=false`。

## Reviewer 输入—输出对照与模板边界

Reviewer 的可信输入包含固定 criterion：“fixture 包含一个被断言的预期结果”，以及 synthetic evidence fact：“fixture 记录了一个通过的 assertion”。Run A 与 Run B 的真实 canonical 输出都不是空模板：两者均生成一条 observation，将 `synthetic-evidence-fact-001` 关联到 `criterion-001`，返回 `contract_shape_supported` 并给出理由。

`m4-matrix-dispatch.sh` 的 `required_object_template` 用于固定完整 JSON 对象、package/context binding 和安全限制，并阻止 `[]`、`null`、wrapper 或 Markdown 破坏严格响应契约。它只允许模型替换 `observations` 与 `missing_package_facts`，且不会在模型失败时被程序作为兜底结果注入。即便输出包含实质 observation，这一路仍只证明 no-tool contract smoke，不证明正式业务质量评审。

Architect 输出中的 `current_evidence_fact_ids` 只列“直接支持 requirement”的 confirmed facts，不是 reason 提到的所有事实。Run B 的 reason 可以引用 `synthetic-fact-001` 来说明它为什么不足以证明完整 Agent workflow，同时保持该数组为空；这与 Run A 将同一事实视作部分 supporting fact 的判断不同，但两份输出都通过引用 allowlist，并都判定最终 requirement 仍为 `gap`。

## 业务闭环证据边界

当前两次 live run 都要求 `business_state_changed=false`；`apply_authorized_change` 在 M4 固定为 `deny_only / M4_APPLY_DISABLED`。因此证据支持“高风险写入不能由模型或 Matrix 文本绕过 State Service”，不支持“人工批准后已经成功写入 V2/V3”。后者如进入后续版本，必须提供独立且明确标注的新运行证据。

## 费用声明

费用数字由本地记录的 token 与固定价格计算。它们证明 Demo 的本地计费逻辑与数量级，但 **远端账户账单没有在本证据包中独立核验**。因此请表述为“本地计算费用”，不要表述为“Provider 官方结算金额”。

## 验证方式

在包根目录运行：

```powershell
.\verify_offline.ps1
```

核验应重新计算包哈希、检查证据 schema/计数与关键关联字段、重算 6 份 canonical Worker 输出，并运行 Demo/evidence/entrypoint 与 selected M4 行为/纯逻辑测试基线；精确用例数由本版本入口运行时输出。它不是对 `src/awakening/` 的全量覆盖率声明。离线核验不会调用 Provider，也不会重放真实 Matrix 消息。

包根目录的三份哈希清单职责不同：

- `PACKAGE_MANIFEST.json`：绑定除自身和 `SHA256SUMS.txt` 外的 payload 路径、字节数与 SHA-256；
- `SHA256SUMS.txt`：绑定除自身外的全部解压文件，因此包含 `PACKAGE_MANIFEST.json`；
- `config/reference-source-pins.json`：只绑定 live 准入所需的 180 个公开源码/契约/Schema/Agent/Skill 文件，用于与参考工作区逐项核对。

前两份用于公开包完整性，第三份用于兼容参考工作区准入；三者都不包含或证明 Secret 值。

`artifact-hashes.json` 中有两类哈希，含义不同：

- `projection_artifacts` 对应本包实际携带的脱敏投影，离线核验会重新计算并逐项比对；
- `source_artifacts` 对应被有意排除的原始冻结日志/结果，只是历史完整性锚点。由于 3A 不把原始模型正文、完整 Matrix 日志和机器运行目录放入公开包，评委无法仅凭本 ZIP 重算这些源哈希；如需深度复核，应由项目方在受控参考环境中出示原件并按锚点核对。

因此本包可以独立证明“公开投影自身完整且内部一致”，并通过两次成功记录、测试与录屏共同佐证真实流程；它不会把“未随包公开的原始文件哈希”夸大成第三方已独立验证的远端证明。
