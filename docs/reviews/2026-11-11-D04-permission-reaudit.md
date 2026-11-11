# D04 权限专项复核

日期：2026-11-11（Asia/Shanghai）。基线提交：`4224658`（工作区干净）。  
范围：权限账本、范围映射、原生回传确认、有效授权与一次消费。  
方法：先核对后修复；仅用临时数据与适配器替身。无真实产品专属会话证据时，结论不得写成「原生审批通过」。

结论标签：通过 / 需修复 / 待验证。

## 总判

D04 平台账本骨架可用，但范围映射过简、有效授权未绑定当前动作、一次消费与事务外回传缺失；两候选适配器仍 `unsupported`。整体 **需修复**；真产品往返 **待验证**。

## 检查表

| 检查位置 | 测试输入 | 期望 | 实际 | 证据 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 授权路径 `permissions/service.py` decide→_forward→confirm | 走完用户决定与回传 | 含操作前再验与授权消费 | 无独立「当前动作可执行」与 once 消费 | 源码 L204–405；runner 仅 `is_effective_grant` | 需修复 |
| Hook/适配器绕过 | 平台 deny、原生 allow | 受控路径不执行 | 平台侧无执行门可拦；原生侧无证据 | 适配器 `unsupported` | 平台逻辑待修后复测；原生 待验证 |
| 默认允许 | 无明确结论 | 不得执行 | 未引入 defer；不明状态不升 confirmed | `_forward` unknown→delivery_unknown | 部分通过 |
| 平台批准≠原生确认 | 用户 approve 无 ack | 非有效批准 | mapping_unsupported / 无 nativeConfirmed | `test_d04_approvals` | 通过（平台） |
| 操作点再验 | execute 时 | 验 digest/会话/attempt/消费 | 只验 confirmed+过期 | `runner/service.py` L155–163；`is_effective_grant` L77–90 | 需修复 |
| `_scope_fits` `bridge.py` L76–83 | `repo/src` vs `repo/src-old`；读≠写；改参命令；缺 scope | 全部拒绝 | 字符串相等/前缀，无文件边界/权限位/命令/网络模型 | 源码 | 需修复 |
| 回传语义 `_forward` L290–328 | once/stage/session | 类型化保留；supported≠确认 | 只传 decision；`supported`+ack 升确认 | 源码 | 需修复 |
| 公开 confirm | source≠adapter_host | 拒绝伪造 | 403 trusted_confirm_required | F06 单测 | 通过 |
| `retry_forward` L390–405 | delivery_unknown | 未知≠未送达，禁止盲重发 | 直接再调 `_forward` | 源码 | 需修复 |
| decide 持会话调原生 | 原生已收、平台提交失败 | 事务外回传+核对 | 同会话内同步调用 | `decide`→`_forward` | 需修复 |
| 一次授权并发消费 | 两请求同 once | 仅一次成功 | 无 consume 原子 | 模型无 consumed 字段 | 需修复 |
| 真产品 Cursor/CLI | 原生往返 | 可证明确认 | respond_approval=unsupported | adapters | 待验证 |

## 修复优先级（本组）

1. 结构化范围校验 + 负例单测  
2. 拆分「记录看似有效」与「当前动作可执行」；once 原子消费  
3. 回传携带 semantic/mapped_options；仅可信 confirmationRef 升确认；reject→rejected  
4. 事务外回传意图 + delivery_unknown 核对（不盲重发）

## 验收口径

- 无真产品证据：最多写「平台逻辑通过」  
- 修复后按小提交重跑受影响路径；不得只靠旧单测宣称收口  
