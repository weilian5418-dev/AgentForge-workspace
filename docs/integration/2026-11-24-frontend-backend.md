# 前后端联调记录 · 2026-11-24

前端：`http://127.0.0.1:5174/workspace`。后端：`http://127.0.0.1:8765/api/v1`。

## 本轮交付

1. 知识库 CRUD：`/projects/{id}/knowledge-documents` 列表/创建/读取/更新/删除（基于 project inputs）。
2. Profile 编辑：`PATCH /agent-profiles/{id}`（label/concurrency/timeout，state_dir 覆盖层）。
3. 任务看板：任务库 list/board 切换，`capabilities.boardView=true`。
4. 扩展：模板跳转新建任务；提供方添加/探测/启停。
5. 终端：`POST /runs/{id}/terminal-sessions` + 命令执行（loopback）。
6. 产物预览：`POST /artifacts/candidates/{id}/previews`。
7. 执行前工作区：`POST /runs/{id}/workspaces/prepare`；前端 `requestExecution` 自动调用。
8. 执行页去掉硬编码运行记录，改为 guidance/消息投影 + 本机终端。

## 实机验证（API）

| 步骤 | 结果 |
| --- | --- |
| 知识库创建/读/更新/删除 | 200/204 |
| Profile PATCH | 200，name=QA Profile |
| 新建 run + 规则计划 | `run_22b2d0f28ac2488c99ed`，compilation valid |
| 新 Claude profile 分配 + 确认 | approved |
| worktree prepare（两角色） | active |
| execution-requests | accepted |
| pause action | accepted |
| terminal session + command | exit 0，stdout hello-from-terminal |

## 边界

- MCP stdio / OAuth 仍按后端支持矩阵诚实 unsupported（HTTP MCP 可用）。
- 外部 Agent 完整跑完到交付验收依赖 worker/付费模型，本轮验证到执行请求受理与暂停。
- 内置浏览器 MCP 本轮不可用；页面能力以 API + 前端接线验证为主。
