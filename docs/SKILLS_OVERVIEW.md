# 9 个 Skill：评委一页总览

[中文导览](JUDGE_GUIDE.md) · [English guide](JUDGE_GUIDE.en.md) · [中文 Skill 总览](SKILLS_OVERVIEW.md) · [English Skills overview](SKILLS_OVERVIEW.en.md)

本页用通俗语言回答三个问题：

1. 9 个 Skill 分别做什么；
2. 哪些 Skill 在比赛 Demo 中真实调用过；
3. 从哪里查看设计、样例、Schema 和真实输出。

> **准确口径：本包包含 9 个 Skill，但不是 9 个都进行了 live 模型调用。**
>
> - `3 live`：Architect、Coach、Reviewer 各有一条真实 Provider 调用路径；
> - `3 contract_only`：契约、Schema 和样例已经定义，但本次 Demo 未激活；
> - `3 deny_only`：安全边界，M4 中只允许失败关闭，不允许成功执行。

Reviewer 虽属于 live Worker，但只运行 no-tool `contract_smoke`：它展示已注册 Reviewer live 路径的身份绑定、固定关闭式 synthetic package 和结构化输出契约闭合，**不是正式业务评审、事实核验、VerifiedClaim 或 M5 验收**。

## 三种状态是什么意思

| 状态 | 通俗含义 |
|---|---|
| `live` | 本次比赛 Demo 中真实经过 Worker → Gateway → Provider，并返回结构化结果 |
| `contract_only` | 设计、输入输出 Schema 和样例完整，但本次 Demo 没有运行该 Skill |
| `deny_only` | M4 中故意只允许拒绝，用来证明高风险能力不能绕过权限、模块或状态边界 |

## 9 个 Skill 总览

| 状态 | Skill / 角色 | 通俗用途 | 主要输入 | 结构化输出 | 本次 Demo 的准确边界 |
|---|---|---|---|---|---|
| **live** | [`analyze_role_gap`](../skills/awakening/analyze_role_gap/SKILL.md)<br/>Architect | 把岗位要求和已确认的用户事实逐项对照，找出已经证明、仍有差距和无法判断的部分 | 岗位事实、已确认用户事实、状态版本、时间约束 | 差距、证据引用和 `unable_to_determine` | 真实调用；只提出分析，不激活计划、不写业务状态 |
| **live** | [`coach_task_submission`](../skills/awakening/coach_task_submission/SKILL.md)<br/>Coach | 按验收标准检查一次任务提交还缺什么证据，并给出修改建议 | 任务及版本、验收标准、已注册证据引用 | 逐标准观察、缺失证据、修改建议 | 真实调用；反馈不具权威性，`certifies_completion=false` |
| **live（范围受限）** | [`review_evidence_against_rubric`](../skills/awakening/review_evidence_against_rubric/SKILL.md)<br/>Reviewer | 用固定关闭式 synthetic package 展示已注册 Reviewer live 路径的身份绑定与结构化契约闭合 | package/context 哈希、固定 rubric、synthetic criteria/facts、`tools_allowed=false` | 契约观察和限制项 | 真实 no-tool 调用；`business_evaluation=false`、`verified_claim_created=false`，不是正式业务评审 |
| **contract_only** | [`design_evidence_project`](../skills/awakening/design_evidence_project/SKILL.md)<br/>Architect | 根据差距和时间约束，设计恰好两个可选证据项目 | gap 引用、时间约束、允许工具、rubric 引用 | 两个项目候选 | 只有契约、Schema 和样例；运行时未激活 |
| **contract_only** | [`build_versioned_plan`](../skills/awakening/build_versioned_plan/SKILL.md)<br/>Architect | 把选定项目拆成带版本、顺序和依赖关系的任务计划 | 基础状态/计划版本、选定项目、周期、任务 | 版本化计划提案 | 只有契约、Schema 和样例；不创建或激活 V2 |
| **contract_only** | [`propose_replan_under_constraints`](../skills/awakening/propose_replan_under_constraints/SKILL.md)<br/>Architect | 当约束变化时，提供 2–3 个不突破约束的调整候选 | 当前计划/版本、synthetic trigger、不可变约束 | 2–3 个 proposal-only 候选 | 只有契约、Schema 和样例；不激活计划、不写业务状态 |
| **deny_only** | [`apply_authorized_change`](../skills/awakening/apply_authorized_change/SKILL.md)<br/>Manager | 保留未来“经授权后申请变更”的窄接口，同时证明 M4 不能成功 apply | proposal ID、状态版本、幂等键、可选 HumanDecision ID | 机器可读拒绝；状态不变 | M4 Registry 固定 `deny_only / M4_APPLY_DISABLED`；没有演示成功写入 |
| **deny_only** | [`distill_experience_candidate`](../skills/awakening/distill_experience_candidate/SKILL.md)<br/>Architect | 定义未来如何从脱敏 Trace 中提炼可复用经验候选 | run/trace 引用、结果、错误引用 | 固定拒绝；不创建、不发布到 RAG | M6 staging 和发布边界不存在，固定失败关闭 |
| **deny_only** | [`generate_evidence_bound_materials`](../skills/awakening/generate_evidence_bound_materials/SKILL.md)<br/>Coach | 定义未来如何只用已激活 Claim 生成简历、作品集或面试材料 | 材料类型、模板引用、active Claim 引用 | 固定拒绝；不生成文字或产物 | M4 无可信 active-Claim readback，固定失败关闭 |

## 每个 Skill 的设计文件和样例

`SKILL.md` 解释设计；`manifest.json` 定义机器可读元数据；`minimal-input.json` 和 `minimal-output.json` 是固定 synthetic 格式样例，**不是运行证据**。

| Skill | Manifest | 输入样例 | 输出样例 | 输入 Schema | 输出 Schema |
|---|---|---|---|---|---|
| `analyze_role_gap` | [manifest](../skills/awakening/analyze_role_gap/manifest.json) | [input](../skills/awakening/analyze_role_gap/examples/minimal-input.json) | [output](../skills/awakening/analyze_role_gap/examples/minimal-output.json) | [schema](../schemas/m4/skills/analyze_role_gap.input.schema.json) | [schema](../schemas/m4/skills/analyze_role_gap.output.schema.json) |
| `coach_task_submission` | [manifest](../skills/awakening/coach_task_submission/manifest.json) | [input](../skills/awakening/coach_task_submission/examples/minimal-input.json) | [output](../skills/awakening/coach_task_submission/examples/minimal-output.json) | [schema](../schemas/m4/skills/coach_task_submission.input.schema.json) | [schema](../schemas/m4/skills/coach_task_submission.output.schema.json) |
| `review_evidence_against_rubric` | [manifest](../skills/awakening/review_evidence_against_rubric/manifest.json) | [input](../skills/awakening/review_evidence_against_rubric/examples/minimal-input.json) | [output](../skills/awakening/review_evidence_against_rubric/examples/minimal-output.json) | [schema](../schemas/m4/skills/review_evidence_against_rubric.input.schema.json) | [schema](../schemas/m4/skills/review_evidence_against_rubric.output.schema.json) |
| `design_evidence_project` | [manifest](../skills/awakening/design_evidence_project/manifest.json) | [input](../skills/awakening/design_evidence_project/examples/minimal-input.json) | [output](../skills/awakening/design_evidence_project/examples/minimal-output.json) | [schema](../schemas/m4/skills/design_evidence_project.input.schema.json) | [schema](../schemas/m4/skills/design_evidence_project.output.schema.json) |
| `build_versioned_plan` | [manifest](../skills/awakening/build_versioned_plan/manifest.json) | [input](../skills/awakening/build_versioned_plan/examples/minimal-input.json) | [output](../skills/awakening/build_versioned_plan/examples/minimal-output.json) | [schema](../schemas/m4/skills/build_versioned_plan.input.schema.json) | [schema](../schemas/m4/skills/build_versioned_plan.output.schema.json) |
| `propose_replan_under_constraints` | [manifest](../skills/awakening/propose_replan_under_constraints/manifest.json) | [input](../skills/awakening/propose_replan_under_constraints/examples/minimal-input.json) | [output](../skills/awakening/propose_replan_under_constraints/examples/minimal-output.json) | [schema](../schemas/m4/skills/propose_replan_under_constraints.input.schema.json) | [schema](../schemas/m4/skills/propose_replan_under_constraints.output.schema.json) |
| `apply_authorized_change` | [manifest](../skills/awakening/apply_authorized_change/manifest.json) | [input](../skills/awakening/apply_authorized_change/examples/minimal-input.json) | [output](../skills/awakening/apply_authorized_change/examples/minimal-output.json) | [schema](../schemas/m4/skills/apply_authorized_change.input.schema.json) | [schema](../schemas/m4/skills/apply_authorized_change.output.schema.json) |
| `distill_experience_candidate` | [manifest](../skills/awakening/distill_experience_candidate/manifest.json) | [input](../skills/awakening/distill_experience_candidate/examples/minimal-input.json) | [output](../skills/awakening/distill_experience_candidate/examples/minimal-output.json) | [schema](../schemas/m4/skills/distill_experience_candidate.input.schema.json) | [schema](../schemas/m4/skills/distill_experience_candidate.output.schema.json) |
| `generate_evidence_bound_materials` | [manifest](../skills/awakening/generate_evidence_bound_materials/manifest.json) | [input](../skills/awakening/generate_evidence_bound_materials/examples/minimal-input.json) | [output](../skills/awakening/generate_evidence_bound_materials/examples/minimal-output.json) | [schema](../schemas/m4/skills/generate_evidence_bound_materials.input.schema.json) | [schema](../schemas/m4/skills/generate_evidence_bound_materials.output.schema.json) |

机器注册状态以 [Skill Registry](../contracts/m4/skill-registry.json) 为准。

## 三个 live Skill 的真实输出

这些文件是两次成功运行中提取并哈希绑定的 canonical Worker 输出，不是 `examples/` 中的格式样例。复核方法见 [EVIDENCE.md](../EVIDENCE.md)。

| live Skill | Run A | Run B |
|---|---|---|
| `analyze_role_gap` / Architect | [canonical 输出](../evidence/run-a/outputs/role_project_architect.json) | [canonical 输出](../evidence/run-b/outputs/role_project_architect.json) |
| `coach_task_submission` / Coach | [canonical 输出](../evidence/run-a/outputs/execution_evidence_coach.json) | [canonical 输出](../evidence/run-b/outputs/execution_evidence_coach.json) |
| `review_evidence_against_rubric` / Reviewer contract smoke | [canonical 输出](../evidence/run-a/outputs/independent_quality_reviewer.json) | [canonical 输出](../evidence/run-b/outputs/independent_quality_reviewer.json) |

`contract_only` 和 `deny_only` 没有 live Worker 输出，这是本次 M4 激活边界的一部分，不是遗漏。

包内哈希只证明公开 canonical 输出与脱敏投影之间的内部一致性；它们不独立证明原始 Matrix 事件、原始 Provider 传输或远端 Provider 账单。

## 评委建议阅读顺序

1. 先读本页，分清 9 个 Skill 的状态；
2. 点开三个 live Skill 的 `SKILL.md` 和 Run B canonical 输出；
3. 再看 [多 Agent 3 分钟导览](JUDGE_GUIDE.md)；
4. 需要技术细节时查看 [系统架构](ARCHITECTURE.md) 和 [证据边界](../EVIDENCE.md)。

---

**English summary:** the package contains nine Skill contracts, but only three were exercised in the live competition Demo. Three are contract-only and three are deny-only safety boundaries. The Reviewer live call is a no-tool contract smoke, not a formal business evaluation.
