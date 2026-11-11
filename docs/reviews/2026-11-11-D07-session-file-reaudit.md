# D07 会话与文件专项复核

日期：2026-11-11（Asia/Shanghai）。基线提交：`4224658`。  
范围：会话/工作区绑定、freeze 停证、清单与归档一致、指定快照集成、恢复/GC/cleanup。  
方法：临时 git 仓与目录；会话恢复成功≠文件恢复成功。

## 总判

D07 具备 worktree/快照/集成接口与部分单测，但停证未验、清单与 tar 非原子、集成取「最新」快照且为全量覆盖、GC 未计入 git 引用、`cleanup --force` 可绕过活跃引用。整体 **需修复**。完整 `run→session→workspace` 链依赖 D09，隔离写证明 **待验证**。

## 检查表

| 检查位置 | 测试输入 | 期望 | 实际 | 证据 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 绑定链 | 双会话不同目录各写标记 | 不串写；恢复会话须再验工作区 | 无 AgentInstance/session 绑定实现 | 包位 `instances/` 空；git 仅 baseline+grant | 待验证 / 结构需补 |
| `freeze_snapshot` L197–215 | 无 ID / 错 workspace / 旧 attempt / 仅请求未确认停止 | 拒绝 freeze | 只写入 `stop_operation_id`，不验 Runner 停止 | `git/service.py` | 需修复 |
| `store_workspace_snapshot` L83–90 | manifest 与 pack 之间改文件 | 失败或清单≡归档 | 两次独立扫盘 | `git/snapshot.py` | 需修复 |
| 符号链接/空目录/删除 | 不支持项 | 显式拒绝或标注 | 跳过 symlink；不表达删除 | snapshot pack/extract | 需修复 |
| `integrate` L268–273 | 指定 snapshotId；A/B 改不同文件；删除；同点冲突；选定后新 freeze | 用锁定版本；删除生效；同点冲突；不换版 | 最新 snapshot；全量覆盖；无删除语义 | `git/service.py` | 需修复 |
| 开发目录事后修改 | 改源仓 | 不影响已固定候选 | 集成基于快照 tar，源仓改不动已冻内容 | 行为部分符合 | 部分通过 |
| 清理后恢复 | cleanup 后按 content_hash 恢复 | 与 manifest 一致 | 有 store.get_bytes；缺强制再算 manifest 对账 API | 单测 freeze 后删 wt | 需补强 |
| GC 引用 | git dirty/workspace/integration 哈希 | 不删 | `_referenced_hashes` 仅 candidate+publication | `artifacts/service.py` L433–439 | 需修复 |
| `cleanup --force` | 活跃 integration / stopped-unknown | 仍拒绝 | force 绕过 integration 引用 | `cleanup` L360–365 | 需修复 |

## 修复优先级

1. freeze 强制可信停证  
2. 单次遍历生成清单+tar，恢复后重算对账  
3. IntegrateRequest 锁定 snapshotIds；删除/冲突语义明确（简化策略须声明边界）  
4. GC 纳入 git_* 内容哈希；force 不得绕过归属/活跃引用  

## 验收口径

会话机制通过不能写成文件恢复通过。无双产品真会话证据标 **待验证**。
