# 安全与 Secret 处理

## 首要规则

**本代码包不包含、也不需要评委提供任何真实 Secret 来完成离线核验。**

不要把以下内容复制到本包、压缩包、截图、录屏、Issue 或聊天中：

- Provider API Key；
- AgentTeams/Matrix/Gateway Token 或内部 Gateway Key；
- 数据库密码；
- `.env`、Docker runtime secret、浏览器会话或账号密码；
- 包含个人信息或项目敏感内容的真实 EvaluationPackage。

`config/` 与 `*.example` 文件只允许出现显式占位符，不允许填入真实值后提交。

## 两种模式的安全边界

### 离线核验

`verify_offline.ps1` 的设计目标：

- Docker 不启动；
- 网络不访问；
- Provider 调用为 0；
- Secret 读取为 0；
- 模型费用为 ¥0；
- 只读取包内代码、配置样例、脱敏证据和哈希。

如果离线入口提示需要真实 Secret，应停止执行并视为包错误。

### 参考环境真实复现

`run_demo.ps1 -Mode LiveStep` 只面向已准备好的 AgentTeams v1.1.2 兼容参考环境，并且必须按 `PrintRunbook` 给出的阶段顺序执行。它可能：

- 启动/停止本地 Docker 容器；
- 访问本地 Matrix/AgentTeams 服务；
- 通过 HTTPS 调用已配置的 Provider；
- 产生实际 token 与费用；
- 在本地 Element/Matrix 留下新的 Demo 消息。

真实模式必须引用参考工作区中已经存在且受保护的运行时配置，不得把 Secret 复制进代码包，也不得输出、hash、回显或上传 Secret 值。

## 已知 Demo 兼容性限制：内部 Gateway Key 的进程参数

参考环境沿用的历史 M4 `Start-M4Agents.ps1` 有一个已知限制：启动阶段的短时 **容器内 `curl` 健康探针** 会把内部 Gateway Key 放在该容器进程的命令行参数中。

这意味着在探针存在的短时间窗口内，具有足够本机/Docker 权限的人可能通过进程检查看到该内部 key。该 key 不是 Provider API Key，但仍应按秘密处理。

项目方为兼容既有参考环境并完成本竞赛 Demo，明确接受了这一限制；它没有被包装成生产级安全设计，也不代表评委或后续部署方必须接受。缓解措施：

- 只在隔离的本地演示机上运行；
- 不与不可信本地用户共享 Docker 权限；
- 运行后按参考流程 Stop/Restore；
- 不在终端、日志或录屏中展示进程参数；
- 后续生产化应改用 stdin、受保护文件描述符或不暴露参数的健康探针。

评委若不接受此限制，只运行离线核验即可。

## 数据边界

随包样例是 fixed synthetic job package。真实模式也应只使用明确批准的演示输入。不要把以下内容发送给 Provider：

- 用户未授权的个人信息；
- Manager/Coach/Human 的自由文本历史；
- 完整 Matrix 历史；
- 工具上下文、Secret 或机器配置；
- 与当前任务无关的 M5/其他模块材料。

## 录屏安全清单

录屏前：

- 关闭通知并隐藏个人账号菜单；
- 不打开 `.env`、Docker inspect 原文、浏览器开发者工具或环境变量；
- 只展示 Manager/Worker 房间中的本次 fresh run；
- 确认画面没有 API Key、Token、密码、绝对用户路径或 PID；
- 使用新的 `demo_request_id`/`demo_run_id` 区分旧消息；
- 录屏文件单独保存与提交，不放入代码 ZIP。

## 发现秘密怎么办

1. 立即停止打包、上传或真实运行；
2. 不要在报告中重复秘密值；
3. 从候选包中移除文件并重新生成清单/哈希；
4. 若秘密可能已离开受控本机，按对应服务流程轮换；
5. 重新运行 `verify_offline.ps1` 和最终 ZIP 解压核验。

## 包发布前最低检查

- 禁止秘密材料文件：`.env*`、运行态凭据文件、数据库文件、Docker config、浏览器 profile；用于安全处理的源码文件名可以包含 `secret`，但文件不得包含真实秘密值；
- 禁止数据内容：真实 API Key/Token/私钥/密码和携带真实值的 Authorization header；运行时代码中用于安全处理的字段名或固定模板不等于凭据值；
- `SHA256SUMS.txt` 与 ZIP 解压后文件一致；
- `evidence/` 不包含失败 run 的原始日志；整包不包含 M5 操作 artifact、决策记录或数据库，也不包含绝对宿主路径和录屏；
- LICENSE 与 NOTICE 随包存在。
