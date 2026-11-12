# D04～D07 专项修复与验证记录

审查基线：8dc365b。本次修改未提交、未推送。以下为实际修复和验证范围，不代表 D03～D07 全部产品/部署验收通过。

## 1. 修复对应关系

| 上轮问题 | 当前处理 | 回归证据 |
| --- | --- | --- |
| 可用普通操作和自写文件冒充停止证明 | D06/D07 共用 Runner 账本验证；核对专用 stopEvidence、真实执行回执、对象/attempt/generation/scope/时间/hash；不读客户端证据文件取得信任 | 伪造文件、普通终态、外部 Agent 状态未知、后续执行使旧证据失效 |
| 审批过期仍发送、异常后重复回传 | 短事务锁定并提交领取，先检查到期/撤销和已绑定对象；原生调用在事务外，结果另事务保存；领取即进入 delivery_unknown | 到期/撤销/旧任务拒绝；进程死亡、超时重投不重复调用；迟到回执不恢复撤销授权 |
| 审批 outbox 无消费者 | approval.forward 类型接入 dispatcher 和 Procrastinate blueprint；HTTP 只记录决定和 outbox；任务绑定唯一派发 operationId | 实际注册 blueprint、任务参数/路由检查；旧 operation 不消费新意图 |
| Runner 持授权事务执行副作用 | API/CLI 统一 execute_runner 协调器：锁定工作区/操作、检查并消费授权、提交 running → 事务外执行 → 事务提交结果 | 实际 PG 独立连接看到 consumed/running 后才执行；执行后进程死亡保留领取，重入拒绝 |
| 一次授权缺少 SQL 到期判定 | 原子 ORM UPDATE 使用 clock_timestamp、状态/撤销/未消费条件；删除数据库异常下的内存降级分支 | 双连接只一胜；旧 ORM/撤销记录；等锁期间到期；数据库异常失败关闭 |
| 只有会话成功就汇总全部恢复 | 默认要求 session/workspace/files 三维度存在、唯一且全部通过；可显式指定更窄检查用途 | 缺维度、重复维度和失败均不通过 |
| 无 context 旧入口绕过/空根/漏 attempt | managed 操作必需 context；期望 attempt 与缺失值也冲突；空、相对、不存在目录拒绝；直接构造 context 再检完整性 | 旧入口、缺期望 attempt、空路径、相对路径回归 |

## 2. 事务与并发边界

- 原生回传：decision/outbox commit → claim commit → 不持数据库事务执行适配器 → 结果 commit。结果保存失败或进程退出时，已提交 claim 阻止盲重发，需可信原生核对后重新派发。
- 同一审批决定的重复 HTTP 请求只复用既有意图。核对允许重试时生成新的派发 operation；旧队列任务不能拿走新意图。
- Runner：claim 与一次授权消费在同一事务提交，执行结果丢失后保持 running，查询/后续调用明确需核对，不能自动重新执行。
- 工作区使用数据库 advisory transaction lock + 注册行锁。Runner 建票/领取、D06/D07 冻结和 stop record 共用锁；冻结后的 Git 工作区不能再领取写操作。
- 所有生产数据库异常向外失败，不调用面向单测的安全降级分支。

## 3. 停止证明的实际保证

`RunnerService.record_stop_evidence` 只为有受信 executionReceipt 的同步 Runner 操作生成账本证明，检查同工作区所有未解决 Runner 操作、已登记 Agent 实例和关联 attempt。调用方从响应 `result.stopEvidence` 提交来源引用；evidencePath 仅作旧字段兼容，不构成真实性来源。

当前支持 coverage=runner_synchronous_only。原生 Agent/后台子执行仍运行或 unknown 时拒绝生成证明；不能把该证明描述成对所有宿主程序、未登记写入者或未来产品异步执行的完整核查。后续原生适配器必须参与相同工作区锁与身份协议，并增加其可信停止来源。

证明绑定 workspaceRoot/workspaceId/attemptId/generation/writableScope、时间和 ledger digest，默认 60 秒内有效；停止之后出现新 Runner 操作会使旧证明失效。D06 单文件冻结与 D07 全工作区冻结使用同一个校验器，不再保留自写 marker 的旁路。

## 4. API/调用方变化

- 审批 decision/retry-forward 返回 forwarding 后由 dispatcher/worker 推进，HTTP 不同步访问原生产品。部署需启动队列派发/消费组件。
- Runner API 和 CLI 使用 execute_runner(engine, request)。RunnerService.claim_execution/finish_execution 仅由事务协调器调用，旧 RunnerService.execute 已移除。
- Runner 票据新增 environmentId/instanceId/sessionRef 可选绑定，原始参数拒绝使用 __ 保留键。最终 ActionDescriptor 包含这些上下文。
- D06 StopEvidenceClaim 使用与 D07 一致的账本证明字段：新增 workspaceId、明确 attemptId、writesQuiesced、checkedAt；没有自动把旧不完整证明当有效。旧客户端须更新。
- export-contracts 已重新生成 52 份文件；其中一些差异来自原本未同步的已实现 API/模型，本次一并对齐，不是新增所有 API 的功能。

## 5. 已执行验证

- 本次专项单元回归：23 项，涵盖原报告复现及事务外副作用、队列路由、停止证明和恢复上下文。
- 全量测试：220 passed / 28 skipped（最后一轮源码行为回归）。28 项跳过中包括需显式测试URL的集成测试和主机不支持的条件用例，不能记通过。
- 新增 PostgreSQL 专项集成：8 passed，显式提供测试连接运行，包含真并发、SQL到期、授权消费与执行意图提交、原生回传并发和进程中断。
- 最近 CLI/契约/API进程回归：31 passed；本次改动范围 Ruff/格式检查通过；修复所涉25个源码文件的定向mypy通过。
- 全项目mypy仍有20项错误（8个文件，含未标注的旧CLI辅助函数），未作为本次7项修复范围扩大重构；日志保存在忽略的environment/results/review-fix-mypy.txt。
- 52份契约快照与当前模型逐份比较一致，重复导出稳定。
- 本次没有真实远程模型、原生产品审批/停止或完整队列集成运行；blueprint注册与派发路由有确定性测试，真实产品语义仍待支持矩阵验证。

PostgreSQL测试创建随机 af_review_* schema，按当前模型建立所需测试表，并隔离移除跨领域外键以验证本次账本/并发机制；结束后删除。本测试不修改 app 现有记录，不应用迁移，也不证明跨领域外键或部署迁移全量通过。当前业务库缺后续 Runner 等表，仍需独立迁移与分权验收，不在本次自动升级。

## 6. 后续边界

ExecutionContext 现在强制存在并校验路径/期望值；完整的数据库分配加载、原生会话及工作区身份实查仍属于 D09/D11，不能把类型构造器当成实际身份验证。

全项目仍可能有其他批次已有静态问题和产品能力缺证据，本次不声称整仓生产就绪。未知结果默认阻塞属于保守恢复策略，人工核对/产品查询机制在实际适配器接入时完成。
