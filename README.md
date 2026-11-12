# AgentForge

本地多 Agent 任务交付后端（Python 3.13 + FastAPI + PostgreSQL + Procrastinate）。  
本仓库已从早期 TypeScript monorepo 脚手架切换为当前 Python 后端；`apps/` / `packages/` / pnpm 栈不再使用。

## 当前范围（代码已落地）

| 批次 | 内容 |
| --- | --- |
| D01 | 模型网关、授权 grant、预算账本、同步调用 |
| D02 | outbox + Procrastinate 异步探测/派发 |
| D03 | 被动发现 + 候选适配器 + profile 矩阵（诚实 unknown/unsupported） |
| D04 | 动作授权账本 + 用户决定 + native 回传状态机 |
| D05 | 受控 Runner（注册 profile / ticket / 受限操作） |
| D06 | 产物总线（freeze → verify → publish；消费需 D10） |
| D07 | 安全 Git worktree / 可恢复快照 / 集成冲突保留 |
| D08 | 只读规划 → 编译校验 → 分配草稿 → 用户确认（不启 Agent） |

未完成或不在本仓库宣称范围：D09 实例与消息、D10 阶段门禁放行、公网托管与完整 OPS。审核意见见 `docs/reviews/`。

## 本机启动

PowerShell 进入本仓库根目录：

```powershell
.\environment\dev.ps1 sync --locked
.\environment\dev.ps1 run agentforge init
.\environment\dev.ps1 run agentforge doctor
.\environment\dev.ps1 run agentforge start
```

API 默认 `127.0.0.1:8765`。另开终端：

```powershell
.\environment\dev.ps1 run agentforge status
```

已具备 Python 3.13 与 uv 时可直接 `uv sync --locked` / `uv run agentforge …`。

## 配置与认证

复制 `.env.example` 为 `.env`（不入库）。配置前缀 `AGENTFORGE_`。  
`init` 在 `.agentforge/credentials/api-token` 写入本机凭据；仅 `/api/v1/health` 匿名。

数据库（可选，应用角色，非管理员）：

```dotenv
AGENTFORGE_DATABASE_URL=postgresql+psycopg://agentforge_api:YOUR_PASSWORD@127.0.0.1:5432/agentforge
AGENTFORGE_QUEUE_DATABASE_URL=postgresql+psycopg://agentforge_driver:YOUR_PASSWORD@127.0.0.1:5432/agentforge
```

迁移使用 `agentforge_migrator` 临时覆盖连接串；服务角色授权见 `deploy/postgres/grants.sql`。期望 Alembic head：`20261111_0013`。

## 开发与验证

```powershell
.\environment\dev.ps1 run ruff check src tests scripts migrations
.\environment\dev.ps1 run ruff format --check src tests scripts migrations
.\environment\dev.ps1 run mypy src
.\environment\dev.ps1 run pytest
.\environment\dev.ps1 run agentforge export-contracts
```

集成测试仅在显式设置 `AGENTFORGE_TEST_DATABASE_URL` 时运行。环境工具说明见 [environment/README.md](environment/README.md)。

## 目录

- `src/agentforge` — 服务与领域逻辑  
- `tests` — 单元/契约测试  
- `migrations` — Alembic  
- `schemas` — 导出的 OpenAPI/JSON Schema  
- `deploy` — Postgres 授权等部署片段  
- `docs/reviews` — 实现审核记录（非产品规格）  
- `environment` — 本机同步与运行 helper  

本次权限、派发、Runner 与停止证明修复的接口变化和验证边界见 [专项修复记录](docs/reviews/D04-D07-fixes-and-validation.md)。
