# 04 · 后端真实契约与接入代码

核对时间2026-11-20；来源为当前本地代码，不是对运行服务做过完整业务验收。路由存在不证明本机adapter、凭据、数据库与执行权限都可用。

## 1. 搬迁原则

现有 `src/api/client.ts`、`session.ts`、`types.ts` 和 endpoint模块主要为纯TypeScript，可作为迁移起点；不要搬React hooks。现有类型有宽泛Record和部分字段简化，后端contracts为更高优先级。

后端 `api/app.py` 明确设置 `openapi_url=None`，所以不能写“curl /openapi.json 即可生成类型”。从后端虚拟环境离线导出，不运行startup/lifespan：

```powershell
Set-Location D:\AgentForge
$taskSchemaTarget = 'D:\AgentFront\apps\workbench-vue\src\data\contracts\contract-snapshot.json'
$env:AF_SCHEMA_TARGET = $taskSchemaTarget
& .\.venv\Scripts\python.exe -c 'import json, os; from pathlib import Path; from agentforge.api.app import create_app; Path(os.environ["AF_SCHEMA_TARGET"]).write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2), encoding="utf-8")'
Remove-Item Env:\AF_SCHEMA_TARGET
Set-Location D:\AgentFront\apps\workbench-vue
pnpm exec openapi-typescript src/data/contracts/contract-snapshot.json -o src/data/contracts/api.generated.ts
```

先创建目标目录。导出如果受配置环境阻塞，应在受控测试Settings下创建app；不能为了导出把`.env`内容打印到控制台。写入schema hash和后端revision/文件hash，生成文件只读，DTO用`components['schemas'][...]`取类型，不手写第二份不一致字段。

## 2. 页面与接口矩阵

所有下表路径除特殊说明外前缀 `/api/v1`。

| 页面/行为 | 已核实路由 | 约束 |
| --- | --- | --- |
| 项目列表/详情 | GET /projects；GET /projects/{id} | 项目与目录grant绑定 |
| 新建项目 | POST /projects；POST /local/directory-grants；/pick | 目录授权不能自动假设 |
| 任务列表 | GET /runs?projectId=&status=&cursor=&limit= | 游标分页；总数若未给不能造 |
| 任务快照 | GET /runs/{id}/snapshot | latestSequence、snapshotToken、guidance |
| 创建任务 | POST /runs | 进入preflight/planning，不自动开发 |
| 计划 | GET /runs/{id}/plan；POST /plan-proposals；POST /plan-revisions | 使用版本与hash |
| 角色分配 | PUT /runs/{id}/assignments | planVersion + assignments |
| 计划确认 | POST /runs/{id}/plan-decisions | 多个version+confirmationRequestId |
| 执行请求 | POST /runs/{id}/execution-requests | 绑定已确认计划，header幂等键 |
| 暂停/恢复/取消/重规划 | POST /runs/{id}/actions | expectedStatusVersion、idempotencyKey |
| 实时事件 | GET /runs/{id}/events?afterSequence= | Bearer fetch SSE；先快照 |
| 实例 | GET /runs/{id}/instances；GET /agent-instances/{id} | 生命周期/忙闲/等待分开 |
| 对话消息 | GET/POST /runs/{id}/messages | 合法payload discriminator、recipientRefs |
| 传递情况 | GET /messages/{id}/deliveries | 收到≠已回复；unknown不能算成功 |
| 审批 | GET /approvals；GET /approvals/{id}；POST /decision | actionDigest绑定具体操作 |
| 原生确认/转发 | POST /approvals/{id}/confirm；/retry-forward | 不可前端自动伪造native确认 |
| 交付 | GET /runs/{id}/delivery；GET /delivery-manifests/{hash} | pointer与manifest配套 |
| 验收 | POST /runs/{id}/acceptance | manifest和status版本校验 |
| 导出 | POST /runs/{id}/delivery-exports | manifestHash+deliveryRevision |
| 产物内容 | GET /artifacts/candidates/{id}/content | 带Bearer，处理二进制与大小 |
| 运行日志 | GET /logs/{log_id} | 只读日志，不是PTY通道 |
| 扫描Harness | POST /agent-products/discover | environments/adapterIds/persist |
| 库存汇总 | GET /agents/summary?environmentId= | 数据来源区分product/profile/instance |
| 创建profile | POST /agent-profiles | installationId+adapterId，非任意表单 |
| profile列表/详情 | GET /agent-profiles；GET /agent-profiles/{id} | 没有已核实的通用PATCH |
| 工具 | GET/POST /tool-servers；GET /tool-capabilities | 内置工具≠MCP服务 |
| 工具probe | POST /tool-servers/{id}/probes?expected_config_version= | 异步operation、目录快照 |
| 模板 | GET /templates；GET /templates/{id}/versions/{version}；POST /templates/imports | YAML导入需格式验证 |
| Provider | GET/POST /providers；GET/PATCH /providers/{id} | 与Harness模型配置区分 |
| Provider凭据 | POST /providers/credentials | 前端只处理写入，不回读secret |
| Provider探测 | POST /providers/{id}/probe | grantId、显式network授权 |
| 知识检索 | POST /retrieval/search；/context-packs；/slices/read；/coverage | 真实project/run/scope/source绑定 |
| 文档 | GET /documents/renderer/status；POST /documents/bundles、/documents/markdown/structure-check、/documents/markdown/preview等 | 文档工件与预览，非知识库全量CRUD |
| Git工作区 | POST /git/worktrees/prepare；/freeze-snapshot；/cleanup | 内部工作区流程，不是任意文件浏览 |
| 集成 | GET /runs/{id}/integrations及相应build/validate流程 | 不等于直接merge按钮 |
| 系统 | GET /health（可匿名）；/ready；/capabilities；/observability/status；/alerts | live状态按实际报告 |

`documents.py` 已核实prefix为 `/api/v1/documents`。当前前端尚未暴露所有后端路由；实施时可补充typed client，不把“前端模块没有”误判成“后端没有”。

## 3. 必须明确的能力缺口

| 设计能力 | 当前证据 | 实施处理 |
| --- | --- | --- |
| 完整知识目录、导入、编辑、版本历史、引用列表 | retrieval能检索/打包/读slice；未发现完整catalog CRUD路由 | 首期读取合法sources/检索，CRUD禁用说明；另提服务端接口草案，不能直接发POST /knowledge |
| 任意profile名称、模型、timeout、权限编辑 | POST profile仅installationId/adapterId，GET返回受控信息 | readonly能力模式；新增编辑契约需后端独立实现 |
| Codex CLI/OpenCode必然可执行 | 图中是设计示例；库存取决于adapter与环境 | 以discovery/profile readiness为准；缺失显示未接入 |
| 交互式终端 | 存在日志read，无已核实PTY/WebSocket | ReadOnlyLog；后续经PTY协议再加xterm.js |
| 任意网页预览 | 产物/renderer能力存在，但非任意浏览代理 | 只使用允许的artifact preview资源 |
| 全部任务总数与页码 | cursor列表 | reference 1/1；live显示已加载数量和前后游标 |
| 原图“创建并开始”立即开发 | RunCreateRequest明确禁止该推断 | 创建、分配、用户确认、execution request分阶段 |
| 浏览器主题保存同时改服务运行方式 | local偏好与backend config不同 | 分开mutation/分项反馈，不假保存 |

这些缺口不应以写死fixture掩盖。reference展示完整目标设计，live按能力给有解释的不可用状态；交付报告列出仍需后端支持的页面区域。

## 4. HTTP客户端与错误

保留现有`ApiError`，增加signal透传、requestId展示、连接配置隔离。请求path只允许应用相对路径，不能把任意外链作为fetch目标自动加Bearer。

```ts
export class HttpError extends Error {
  constructor(public status: number, public requestId: string | null, public responseBody: string) {
    super(`HTTP ${status}`)
  }
}

export function createApiClient(getToken: () => string | null, base = '/api/v1') {
  return async function request<Result>(path: string, init: RequestInit = {}): Promise<Result> {
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('API path must be relative to configured base')
    const token = getToken()
    if (!token) throw new Error('请先连接后端服务')
    const headers = new Headers(init.headers)
    headers.set('Authorization', `Bearer ${token}`)
    headers.set('Accept', 'application/json')
    if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    const response = await fetch(`${base}${path}`, { ...init, headers })
    if (!response.ok) throw new HttpError(response.status, response.headers.get('X-Request-ID'), await response.text())
    if (response.status === 204) return undefined as Result
    return response.json() as Promise<Result>
  }
}
```

JSON request与multipart上传分开；不能给FormData强加application/json。业务错误读取统一error.message/detail，日志脱敏，UI只呈现合适的短消息。401退出连接态；403权限不足；409要求重新核对版本；429尊重Retry-After；5xx可重试读取；POST/approve/accept等不能无条件自动retry。

登录token仅由现有local连接/交接流程获得；优先内存会话，刷新恢复方案沿用后端受控流程。不要把用户提供的生图Key写进session，不要把后端Bearer放URL/query或任何VITE变量。

## 5. 创建→规划→确认→执行

正确流程：

```text
填写目标/项目/输入资料
  → POST /runs（固定idempotencyKey）
  → GET snapshot + plan
  → 如需规划且后端未自动提出，POST plan-proposals
  → 显示实际roles/workPackages/依赖/风险
  → PUT assignments（实际roleId/agentProfileId）
  → 重新GET plan，读取confirmation与各版本
  → 用户明确确认当前计划/分配
  → POST plan-decisions（confirmation绑定）
  → 如需显式execution request，使用确认后的binding提交
  → 202 pending，读取operation/快照/SSE
  → 进入真正执行中
```

createRun示例body（枚举已核对）：

```ts
const body = {
  projectId,
  goal,
  mode: 'team',
  outputs: ['code'],
  executionProfile: 'host_authorized',
  failurePolicy: 'dependency-aware',
  budget: { maxWallTimeSeconds: 1800 },
  idempotencyKey: creationRequestKey,
}
```

`host_authorized`只能在真实目录授权/环境策略满足时选用；否则使用可用的container，不替用户绕过授权。`inputRefs`可从生成schema补上；原旧前端createRun wrapper没有该字段应修正wrapper而非丢附件。

分配字段实际是 `agentProfileId`，不是示例PlanResponse简写中的`profileId`：

```json
{
  "planVersion": 3,
  "assignments": [
    { "roleId": "actual-frontend-role-id", "agentProfileId": "actual-profile-id" }
  ]
}
```

确认body必须来自当前 `plan.confirmation`：decision、confirmationRequestId、planVersion、planHash、assignmentVersion、gatePolicyVersion、statusVersion。409时重新加载、重新展示，不能把最新版本自动换上后继续approve。

execution request有planHash、planVersion、assignmentVersion、gatePolicyVersion，幂等键是 `Idempotency-Key` header。202仅表示accepted/preparation，不等于所有阶段放行或工具开始工作。`OperationAccepted`按返回kind选择对应operation查询；不要把所有operation都查询到providers的 `/operations`。

## 6. SSE：认证、游标、重连与刷新

已有 `openRunEventSource()` 不适合Bearer（EventSource不能设置Authorization）。新的实现使用fetch和真正SSE parser，不能延用简单按`\n\n`拆分（CRLF、跨chunk、控制帧需要正确处理）。

先GET snapshot，并从同一快照的 `latestSequence` 开流。业务数据有eventId/sequence/type/payload；控制帧有event/reason，没有业务sequence。

传输层可用代码：

```ts
import { createParser } from 'eventsource-parser'

export interface SseFrame { event: string; id?: string; data: unknown }

export async function readRunStream(input: {
  runId: string
  afterSequence: number
  token: string
  signal: AbortSignal
  onFrame: (frame: SseFrame) => void
}) {
  const response = await fetch(`/api/v1/runs/${encodeURIComponent(input.runId)}/events?afterSequence=${input.afterSequence}`, {
    headers: { Authorization: `Bearer ${input.token}`, Accept: 'text/event-stream' },
    signal: input.signal,
  })
  if (!response.ok) throw new HttpError(response.status, response.headers.get('X-Request-ID'), await response.text())
  if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) throw new Error('无效的事件流响应')
  let parseFailure: Error | undefined
  const parser = createParser({
    onEvent(message) {
      if (parseFailure) return
      try { input.onFrame({ event: message.event ?? 'message', id: message.id, data: JSON.parse(message.data) }) }
      catch (error) { parseFailure = error instanceof Error ? error : new Error('事件格式错误') }
    },
  })
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  try {
    while (!input.signal.aborted) {
      const chunk = await reader.read()
      if (chunk.done) break
      parser.feed(decoder.decode(chunk.value, { stream: true }))
      if (parseFailure) throw parseFailure
    }
    parser.feed(decoder.decode())
    if (parseFailure) throw parseFailure
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
```

`HttpError`从HTTP模块导入。此函数只传输，不负责业务reducer，也不会自动重连。外层supervisor必须落实以下状态机：

1. `connecting → live → retrying → live`；离开run、切项目、鉴权撤销立即abort，清理timer；多组件共享一条run连接。
2. 首版采用“事件失效通知+合并刷新”策略：事件先校验runId/类型；按eventId去重、忽略≤latestSeenSequence；250ms批量invalidate相关queries，避免每token全页refetch。
3. `latestSeenSequence`表示收到了事件，`snapshot.latestSequence`表示服务端一致快照。不能将两者混成一个字段。初始和需要重新同步时用快照cursor；普通重连可保守重新读取快照再开流，避免漏掉仅通知但尚未刷新出的状态。
4. 同一runId建立connection generation；旧流/旧请求迟到不能写入新任务。runId watcher每次创建新的controller和generation，queryFn signal真正传给fetch。
5. EOF、网络中断使用指数退避1/2/4/8/最多30s并带jitter；保持旧可读数据，显示断线状态。重连成功后refetch/invalidate本任务的plan/messages/approvals等，不能只刷新snapshot。
6. HTTP409或control `resync_required`/`snapshot_required`：关闭流，重新取snapshot，记录新cursor，重取受影响query后开流。不要直接sequence++继续。
7. `auth_revoked`、401停止，不循环使用失效token；`server_draining`退避；`stream_limit_reached`/429按Retry-After/上限退避，不同时开12个连接。
8. 控制帧不计业务sequence、heartbeat注释由parser忽略；未知事件触发保守刷新，保留可观测日志而不崩整个页面。
9. 不要求每个公开事件sequence严格+1；公开投影可能不包含所有内部事件。出现明确server gap/resync信号才按其契约重拉；不能把普通跳号当损坏。
10. seen-event集合有界，例如最近2000个；live数据和query缓存恢复后清理旧历史。首次网络慢时不要出现假“已连接”。

流返回原生事件并不总能重建自然语言聊天正文。Conversation使用messages API读真实payload，SSE作为失效通知；工具日志、产物列表分开读。后续需要token级增量输出时先定义明确的公开消息增量事件协议。

## 7. 审批代码：绑定用户看过的摘要

```ts
import { ref } from 'vue'

const deciding = ref(false)
const approvalError = ref<string | null>(null)

async function approveDisplayed(displayed: { id: string; actionDigest: string; status: string }) {
  if (deciding.value) return
  deciding.value = true
  approvalError.value = null
  try {
    const latest = await getApproval(displayed.id)
    if (latest.actionDigest !== displayed.actionDigest || latest.userDecision || Date.parse(latest.expiresAt) <= Date.now()) {
      approvalError.value = '审批已更新或失效，请重新查看后决定。'
      await refreshApprovals()
      return
    }
    await decideApproval(displayed.id, { decision: 'approve', actionDigest: displayed.actionDigest })
    await refreshApprovals()
  } catch (error) {
    approvalError.value = formatApiError(error)
    await refreshApprovals()
  } finally {
    deciding.value = false
  }
}
```

这里的getApproval、decideApproval、formatApiError来自原API模块迁移；refreshApprovals由query invalidate包装。不写nativeConfirmed=true，不自动调用confirm。提交后按真实forwardState显示“已记录决定/等待执行者确认/已确认/需处理”。

## 8. 验收、下载与能力门控

验收示例body：

```ts
const request = {
  decision: 'accept',
  integrationVersion: displayedDelivery.integrationVersion,
  manifestHash: displayedDelivery.manifestHash,
  manifestRevision: displayedDelivery.manifestRevision,
  expectedStatusVersion: displayedRun.statusVersion,
  note: acceptanceNote,
}
```

提交时禁用按钮、保留展示版本；409要求重新检查；请求超时结果未知时先GET状态，不自动生成新请求重复验收。partial_accept的memberIDs从manifest取，不能用文件名代替。

按功能粒度使用能力状态：

```ts
type Availability =
  | { supported: true }
  | { supported: false; reason: string }

const editProfile: Availability = {
  supported: false,
  reason: '当前执行环境暂未提供此项配置的修改接口',
}
```

能力来自已核实contract+环境响应，不能仅通过“服务器返回200”判所有按钮可用。缺接口不必使整页消失；读数据可用、写操作不足时保持表单只读，给一个清楚的说明和返回路径。

## 9. 业务测试清单

- 创建重复点击只产生一个run；刷新后仍能恢复确认阶段。
- 计划改版后旧confirmation不被提交；assignments使用真实agentProfileId。
- SSE跨chunk、CRLF、多data行、注释、重复event、乱序event、controlframe、409、401、429、EOF均测试。
- 从A任务切B任务，A的慢响应/事件不能改B。
- 审批actionDigest变化/过期/native未知时不会误判批准成功。
- 验收manifest变更、statusVersion过期、部分接受ID非法均有恢复路径。
- binary artifact不能直接TextDecoder当文本；下载objectURL销毁；未授权内容不可预览。
- 扫描结果未知与未配置不显示绿色；可调度数量来源正确。
- live请求失败不回落成fixture成功；正式构建不能开启reference路由开关。
