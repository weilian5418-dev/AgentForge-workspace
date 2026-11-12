# D11 故障矩阵验收分级（2026-11-12）

本报告对照 `docs/plans/D11_平台协作图与恢复_详细实现方案_2026-11-12.md` §16。  
**不把 mock / 替身自动化当作两个真实产品已可用。**

## 分级定义

| 级别 | 含义 |
| --- | --- |
| mock | 单元/内存替身；证明平台策略与契约 |
| pg | 真实 PostgreSQL（`AGENTFORGE_TEST_DATABASE_URL` + 迁移 0017） |
| fs | 真实文件系统路径/worktree 身份 |
| product | 已验证原生适配器 + 受信 Host；当前多为 pending |

## 矩阵覆盖

| 场景 | 级别 | 证据 |
| --- | --- | --- |
| 同 Run 两 Driver / lease 失效 | mock + pg | `tests/unit/test_d11_driver_fencing.py`；`tests/integration/test_d11_driver_constraints.py::test_stolen_lease_rejects_old_fence` |
| 图连接断开 | mock | fence 丢失即 `DriverFenceLost`；不自动沿用旧身份 |
| 事务后 checkpoint 前崩溃 / 原生后回执前崩溃 | mock | unknown attempt → `open_recovery`；dispatch 预留唯一键 |
| 等待时重启 / 先事件后 wait / 重复唤醒 | mock + pg | `tests/unit/test_d11_waits_signals.py`；wake/wait 唯一约束 integration |
| 多个未决审批 | mock | wait 按 key 独立登记（容量/审批 reason） |
| 下游提前派发 | mock | `ReleasedInputResolver` + planner 不跳过未放行上游 |
| 同实例两工作包 / unknown 工作槽 | mock | planner capacity / unknown → recovery |
| 暂停与发送竞争 | mock | `test_d11_fault_matrix` + RunControl + dispatch admission |
| 取消远端未停 | mock | cancel 保持 `cancel_requested` |
| drain 遗漏扁平状态 | mock | `collect_drain_report` 列出 failed/created 背后 unknown |
| 原会话仍在运行 | mock | recovery policy `observe_existing` |
| 原生不支持恢复 | mock | `handoff_required` / unsupported |
| 恢复中变计划 | mock + pg | apply `caseVersion`/`contextHash`/`statusVersion` 冲突 |
| 哈希变化 / 工作区偏离 | mock | recovery workspace mismatch → block_or_rework |
| 登录过期 | mock | native support=unknown → await |
| 图 schema 不兼容 | mock | binding_hash / state_schema_version 写入契约 |
| 集成交接边界 | mock | `prepare_handoff` → `ready_for_integration`，不 completed |
| Git 完成后断电 | fs/pending | D07 路径认领；D11 纵向场景未在本机产品跑通 |
| 真实 Cursor/CLI Agent 端到端 | product/pending | 需受信 Host；见下方脚本 |

## 如何跑

```powershell
# mock 级（默认 CI）
.\.venv\Scripts\python.exe -m pytest tests/unit/test_d11_*.py -q

# PostgreSQL 级（需隔离库 + 0017）
$env:AGENTFORGE_TEST_DATABASE_URL = "postgresql+psycopg://..."
.\.venv\Scripts\python.exe -m pytest tests/integration/test_d11_*.py -q -m integration
```

## 产品验收脚本

`scripts/acceptance/d11_vertical_stub.py` 只打印最小纵向步骤与当前阻断条件，**不会伪造 product=pass**。
