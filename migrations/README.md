# 数据库迁移

## 当前 head：`20261111_0011`

| Revision | 说明 |
| --- | --- |
| `20261110_0001` | 既有 10 张 `app` 表基线（表名齐全时跳过建表；**不**等于结构已校验） |
| `20261110_0002` | BE-02：BIGINT 序号、`events.schema_version`、复合 FK、outbox/幂等列、审计表授权收紧 |
| `20261110_0003` | v1.4：规划模型字段重命名、运行状态扩展、协作对象表；**拒绝破坏性 downgrade** |
| `20261110_0004` | 投递/门禁 FK、CapabilityCheck/Submission/Review/Integration 表；**不**猜测改写等待状态 |
| `20261110_0005` | 文档化：禁止启发式区分两类等待 |
| `20261110_0006` | 等待状态核对账本；歧义 run 隔离为 `blocked`；空集合也留批次证据 |
| `20261111_0007` | BE-04：目录授权、环境、基线、输入、路径绑定；projects 增补列 |
| `20261111_0008` | D01：provider/授权/预算/调用账本与能力检查 |
| `20261111_0009` | D02：async_operations + Procrastinate queue schema |
| `20261111_0010` | D05：runner_operations 受控执行账本 |
| `20261111_0011` | D06：content_objects / candidates / validation / publications |

结构核对：

```powershell
$env:AGENTFORGE_DATABASE_URL = "postgresql+psycopg://agentforge_migrator:agentforge_migrator_dev@127.0.0.1:5432/agentforge"
.\environment\dev.ps1 run python scripts/verify_baseline.py
.\environment\dev.ps1 run alembic upgrade head
```

授权脚本：`deploy/postgres/grants.sql`（`schema_migrations` 对 api/driver 只读）。

| 表 | 用途 |
| --- | --- |
| projects | 已授权项目目录 |
| runs | 任务运行（`status_version` / `active_generation` / `last_sequence`） |
| plans | 计划版本与契约哈希 |
| work_packages | 工作包（含 `(id, run_id)` 唯一） |
| task_attempts | 尝试租约（复合 FK 防跨 run） |
| events | 业务事件序号 |
| outbox | 事务 outbox |
| run_leases | Graph Driver 运行租约代次 |
| idempotency_requests | 请求幂等 |
| schema_migrations | 旧版手工迁移审计（服务只读） |
| agent_products / agent_installations / agent_profiles | 本机产品发现与可调度配置 |
| role_assignments / agent_instances | 角色分配与外部 Agent 实例（≠ 平台 API 进程） |
| stages / gate_decisions | 阶段门禁与放行决定 |
| collaboration_messages / message_deliveries | 结构化消息与投递账本 |
| approval_mappings | 用户决定 ↔ 原生审批回传 |

`queue` / `graph` 由组件迁移；`index` 由项目自有迁移管理。

日常 `.env` 使用 `agentforge_api`。Alembic 仅用 `agentforge_migrator` 临时覆盖。禁止用 `stamp` 掩盖漂移；禁止改已应用 revision 隐藏差异。baseline/`0002` 的 `downgrade` 拒绝执行，回退靠备份。`0003` 提供可逆 downgrade，但仍优先用备份恢复。
