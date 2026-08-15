# 系统架构

## 一句话说明

Awakening Demo 使用 AgentTeams 的 Manager/Worker 拓扑，由确定性的 Manager 策略/契约控制面把一份固定 synthetic job package 同时交给三个已注册角色，由三个 Worker 生成结构化结果，再按固定规则汇总，并通过 Matrix/Element 把阶段流转展示给人。该 Demo 不支持模型自主路由或 LLM planner。

## 角色

| 角色 | 标识 | 责任 | 是否调用模型 |
|---|---|---|---:|
| Manager | `default` / `awakening_program_manager` | 按固定角色—Skill 映射接受请求、生成三路任务、并发分派、校验/关联结果、写阶段事件并确定性汇总 | Demo 中为 `0` |
| Architect Worker | `role_project_architect` | 给出角色/项目架构方案 | 是 |
| Coach Worker | `execution_evidence_coach` | 给出执行与证据改进建议 | 是 |
| Reviewer Worker | `independent_quality_reviewer` | 对关闭式 synthetic package 做 no-tool contract smoke；不作正式业务评审 | 是（1 次 contract-smoke live call） |

## 运行数据流

```text
fixed synthetic request
        |
        v
Demo orchestrator / deterministic Manager route
        |
        +--> Matrix: request-accepted
        |
        +--> Architect room --> Gateway --> Provider --> structured result
        |
        +--> Coach room ------> Gateway --> Provider --> structured result
        |
        `--> Reviewer room ---> Gateway --> Provider --> contract-smoke result
                    (three worker calls may be concurrent)
        |
        +--> Matrix: worker-dispatched x3
        +--> Matrix: worker-completed x3
        `--> Matrix: summary-completed x1
        |
        v
result.json + packages + lifecycle + safe evidence projection
```

Manager 自身不调用模型；它执行确定性的策略、契约校验、编排、关联和汇总。角色集合、Skill 映射与调用计划均由代码/注册表冻结，不把“选择哪个 Worker、调用什么工具、是否改计划”交给模型。三个 Worker 分别通过各自 Gateway/身份调用 Provider。

并发有两层数字，不能混用：实现对单 run 的 Worker Provider 调用总数设硬上限 `3`，同时在途调用也设硬上限 `3`。`ObservedProvider` 在调用开始前用锁增加在途计数，在 `finally` 中减少，并保存进程内观察到的最大值；运行证据中的 `max_inflight=3` 因而是两次成功 run 的**运行时计数器观测**。公开安全投影有事件顺序和哈希，但没有逐调用 `started_at/ended_at`，所以它能绑定已记录峰值，不能让第三方只靠公开文件独立时序重放并重新计算峰值。两次运行的 retry 与 hidden retry 都为 `0`。

Reviewer 的 Provider 请求不携带可调用工具，只对关闭式 synthetic fixture 验证输入/输出 contract shape。其 canonical 输出明确为 `reviewer_mode=contract_smoke`、`business_evaluation=false`、`verified_claim_created=false`。因此它证明 Reviewer live 路径和结构化契约可以闭合，不证明真实业务证据已经通过独立评审，更不是 M5 验收。

Reviewer contract 中保留 `required_object_template` 是一项输出可靠性约束：它固定顶层对象、package/context hash、限制项和两个恒为 `false` 的安全字段，并禁止 `[]`、`null`、wrapper 与 code fence。模型仍需依据可信 package 生成变量字段 `observations` 与 `missing_package_facts`；两次随包 canonical 输出都生成了一条非空 observation。模板不作为超时、解析失败或模型失败时的兜底结果。

```text
trusted criterion: fixture contains one asserted expected result
trusted evidence fact: fixture records one passing assertion
Reviewer observation: criterion-001 = contract_shape_supported
                      cites synthetic-evidence-fact-001 and gives a reason
```

这只是“固定 criterion 与 evidence fact 的契约级关联”，不是对求职者真实项目、证据充分性或录用价值的独立业务判断。

## 为什么 Manager 里看不到全部 Worker 正文

当前拓扑是 **Manager 与每个 Worker 的独立房间**，不是四个 Agent 全部加入同一群聊：

```text
Manager <-> Architect room
Manager <-> Coach room
Manager <-> Reviewer room
```

因此：

- Worker 房间能看到 Manager 发给该 Worker 的任务和该 Worker 的回复；
- Manager 主房间主要看到生命周期阶段与最终汇总；
- Worker 的长 JSON 不一定逐字复制回 Manager 主房间；
- 编排层通过关联 ID 收集 Worker 结果，然后生成 `summary-completed`。

这是一种合理的点对点代理通信方式。若未来希望“一个房间里看见四方完整对话”，需要额外实现公共观察室/事件镜像，但那不是本次固定 Demo 的必要条件。

## 契约与 Skill

关键目录：

- `agents/m4/`：四个角色的身份文件；
- `skills/awakening/`：Skill 定义、manifest 与最小样例；
- `contracts/m4/`：身份、Skill、命令、Gateway 与 reason code registry；
- `schemas/m4/`：ContextManifest、运行时配置、Provider usage receipt、Skill receipt 与各 Skill I/O schema；
- `src/awakening/`：编排、Gateway、State 与适配层实现；
- `scripts/demo/`：Demo 编排和参考入口；
- `infra/agentteams/`：AgentTeams/Matrix 参考配置与容器内脚本。

Worker 输出必须满足角色对应的结构化契约。Manager 使用 `demo_request_id`、`demo_run_id` 和 evidence hash 把 Matrix 事件、Gateway 结果与最终摘要关联起来。

### 9 个 Skill 的实际激活范围

| 分组 | Skill | `m4_activation` | 本竞赛 Demo 中的含义 |
|---|---|---|---|
| live | `analyze_role_gap` | `representative_live_call` | Architect 发起真实 Provider 调用并返回 schema-valid 结果 |
| live | `coach_task_submission` | `representative_live_call` | Coach 发起真实 Provider 调用并返回 schema-valid 结果 |
| live | `review_evidence_against_rubric` | `contract_smoke_live_call` | Reviewer 发起 no-tool contract-smoke 调用；非正式业务评审 |
| contract_only | `design_evidence_project` | `contract_only` | 仅提供 manifest/schema/样例；运行时未激活 |
| contract_only | `build_versioned_plan` | `contract_only` | 仅提供 manifest/schema/样例；运行时未激活 |
| contract_only | `propose_replan_under_constraints` | `contract_only` | 仅提供 manifest/schema/样例；运行时未激活 |
| deny_only | `apply_authorized_change` | `deny_only` | 固定 fail closed：`M4_APPLY_DISABLED` |
| deny_only | `distill_experience_candidate` | `deny_only` | 固定 fail closed：`M6_EXPERIENCE_STAGING_UNAVAILABLE` |
| deny_only | `generate_evidence_bound_materials` | `deny_only` | 固定 fail closed：`ACTIVE_CLAIMS_UNAVAILABLE_IN_M4` |

所以“9 个 Skill 随包”不等于“9 个 Skill 都进行了 live 模型调用”；准确口径是 `3 live + 3 contract_only + 3 deny_only`，其中第三个 live 是范围受限的 Reviewer contract smoke。

### M4 审批与业务写入边界

本 Demo 有意不演示成功 apply。`apply_authorized_change` 只接受 ID/version 字段，但在 M4 Registry 中必须保持 `deny_only`，State MCP 返回 `M4_APPLY_DISABLED`，成功 Demo 还要求前后 `state_version` 与 active plan 不发生改变。该设计证明模型和 Matrix 消息不能绕过 State Service 形成业务写入；它不证明人工批准后的 V2/V3 已在本 AgentTeams Demo 中完成。若未来纳入审批通过链路，必须作为新的受控集成证据，而不能把离线 State 测试冒充当前 live run。

### 上下文与 RAG 边界

当前输入是小规模、冻结且有哈希绑定的 synthetic trusted package，系统通过共享状态投影、ID-only context 与可观察轨迹交接信息。在这一范围内加入向量检索不会增加证据可信度，因此 M4 明确不启用 RAG。若迁移到持续变化的岗位库、企业知识库或工单历史，可在 package 构建前增加带来源、版本与引用的检索适配层；Agent 身份、Skill Schema、State 写入和审计边界仍可复用。这里的“不启用”是基于数据范围的工程选择，不是声称检索能力已经实现。

### `current_evidence_fact_ids` 的语义

该字段表示“直接支持当前 requirement 的 confirmed fact”，不是 reason 中出现过的全部 fact ID。Architect 可以在 reason 中说明某条已确认事实为何**不足以支持**更宽的 requirement，此时 `current_evidence_fact_ids=[]` 是符合契约的；若事实确实提供直接支持，才把其 ID 放入数组。随包 Run A 选择将单元测试事实视为部分支持，Run B 将其视为不足以支持完整 Agent workflow，二者都得出 `status=gap`。这属于受限模型判断差异，不应通过字符串搜索强制两者相等。

### 固定 JSON 为什么存在于 Gateway credential probe

`infra/agentteams/demo/runtime/demo-worker-gateway-key-sync.sh` 中硬编码的 JSON 是**live 凭据 preflight/probe 的 contract fixture**，不是公开入口的 `-Mode Preflight`（后者不读取 Secret）。该 fixture 只用于验证 Worker 能携带现有内部 Gateway 凭据到达 fail-closed 边界：预期 Gateway 返回 `403 / CALL_PLAN_UNAVAILABLE`，固定标记 Provider 调用数为 `0`。它不是人工输入、不是业务任务，也不应到达 Provider。该 shell 属于冻结的哈希绑定参考实现，因此本次文档纠偏不修改它；离线测试只验证其固定请求、固定失败边界和 Secret 不回显契约。

另一个容易混淆的位置是 `infra/agentteams/m4/runtime/m4-matrix-dispatch.sh` 的 `worker_contract_filter_preflight()`。其中 Architect、Coach、Reviewer 三段固定 JSON 是**本地 Worker 输出契约过滤器的正向自测 fixture**，并与同函数中的负向 fixture 配套使用；它们只验证解析/拒绝边界，不是模型失败时的兜底结果，不会作为 Worker 的真实输出，也不会伪装成 Provider 返回值。该文件同样属于 180 个参考源码 pin，因此只在此说明其用途，不为文案目的改动冻结源码。

## 状态与可观察性

每个成功 Demo 保留以下最小可观察对象：

- request/run ID；
- 三路 dispatched/completed 状态；
- Provider begin/succeeded/failed/end 计数；
- token 与本地费用计算；
- Matrix exact 8 阶段事件；
- lifecycle 事件；
- 结果、Matrix、生命周期等文件的 SHA-256；
- Stop/Restore 与无 Demo listener 的结论；
- 两个 run 各三份 canonical Worker 输出，以及与 provider event output hash/schema 的绑定。

公开包只放安全投影，不放完整宿主/容器/数据库上下文。

## 离线测试覆盖边界

`verify_offline.ps1` 的默认测试基线分为两组：`tests/unit/demo/` 覆盖入口 fail-closed、参考 profile、关键调用/Secret 边界、证据关联、6 份 canonical 输出与 Demo runtime contract；`tests/unit/m4/` 覆盖随包闭包内被选择的 M4 行为与纯逻辑，包括 mock-subprocess 的 Matrix delegation port。精确用例数由本版本入口运行时输出；它不是覆盖率百分比，本包也没有声明对 `src/awakening/` 全部模块实现了完整行覆盖、分支覆盖或生产级集成测试。

## 双层复现设计

### Layer 1：独立离线核验

输入是本包自身。验证代码、契约、样例、证据和哈希，不访问外部系统。适合评委快速、低风险复核。

### Layer 2：兼容参考环境实时复现

输入是本包入口加一个已经准备好的 AgentTeams v1.1.2 兼容工作区。该环境提供 Matrix、容器、账号、运行时 identity 与受保护 Provider 配置。适合项目方现场复现或评委在等价环境中重跑。

两层的声明不同：Layer 1 能独立证明“材料内部可检查”；Layer 2 才能再次证明“当前环境可实际流转”。

## 非目标

- 自动部署全新 AgentTeams/Matrix 集群；
- 生产级 Secret 管理或多租户隔离；
- M5 模块验收；
- 在公共群聊中镜像所有内部消息；
- 对远端账单做财务审计；
- 把 synthetic Demo 宣称成真实用户生产结果。
