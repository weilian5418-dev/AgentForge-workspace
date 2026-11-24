# AgentForge 前端

Vue 3 + TypeScript 工作台。开发页面：`http://127.0.0.1:5174/workspace`。API 通过 Vite 代理连接 `http://127.0.0.1:8765/api/v1`。

## 启动联调环境

后端默认位于 `D:\AgentForge`，应已配置 Python 环境、PostgreSQL、迁移和本机凭据。从 `D:\AgentFront` 执行：

```powershell
.\scripts\start-dev.ps1
```

脚本复用现有服务，缺失时后台启动前端与控制 API，并保留日志。数据库迁移、worker 进程独立管理。若已运行的 API 拒绝前端 Origin，在后端 `.env` 的 `AGENTFORGE_ALLOWED_ORIGINS` JSON 数组中加入 `http://127.0.0.1:5174` 后重启。

页面默认使用真实 API，启动时通过本机 handoff 获取凭据，仅保留在内存。连接失败可在“设置 → 服务连接”重试；API 密钥留空会获取本机凭据。不要把密钥写入前端环境变量。

单独启动前端：

```powershell
cd D:\AgentFront\apps\workbench-vue
pnpm install --frozen-lockfile
pnpm dev
```

`VITE_ENABLE_REFERENCE=true` 仅启用设计预览入口。显式访问 `?reference=1` 才使用演示数据，普通 URL 使用真实接口。

## 当前接入范围

- 认证、健康与数据库就绪状态、项目选择。
- 任务创建、参考文件上传、规则计划、分配与校验、计划确认和执行请求。
- 任务快照、具体实例消息、审批、暂停/恢复请求、SSE 事件及重连。
- 本机执行者扫描、配置注册与详情、库存。
- 交付清单、检查记录、候选文件预览/下载和绑定版本的验收提交。
- 工具服务列表、HTTP 草稿注册与探测请求，模板和模型提供方列表。

无运行实例时消息按钮禁用；能力不足或证据过期会显示后端原因。知识库完整 CRUD、通用配置编辑、交互终端和 MCP stdio/OAuth 等尚未提供的能力保留限制。

## 验证与契约

`src/data/contracts/contract-snapshot.json` 来自当前 FastAPI OpenAPI；更新后生成类型：

```powershell
pnpm exec openapi-typescript src/data/contracts/contract-snapshot.json -o src/data/contracts/api.generated.ts
pnpm typecheck
pnpm test:unit
pnpm build
```

实际联调证据与验证边界见 `D:\AgentFront\docs\integration\2026-11-21-frontend-backend.md`。
