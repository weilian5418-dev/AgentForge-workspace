# 01 · 唯一视觉契约

## 1. 参照优先级

优先级由高到低：用户明确决定 → 本文件统一规则 → `03-execution.png` 的共享外壳 → 各页 PNG 的内容布局 → 原始生图提示词 → 组件库默认样式。

原始提示词常写了 240px 侧栏和 70px Header，但成图实际更接近 296–314px 侧栏和 72px Header。实施按成图和本规范，不能拿提示词覆盖成图。生成图中的坏字、无意义代码、错误状态不可原样作为业务逻辑复制。

原图未提供真正的字体文件；本文件给出工程化字体方案。字体替换会改变字宽，所以在字体确认前不能声称达到逐像素一致。发现原图差异时提交截图和坐标，不能自行重新设计。

## 2. 画布与共享外壳

标尺单位全部为 CSS px；基准视口 2048×1152，DPR=1，浏览器缩放 100%，滚动条行为固定。图中一个像素对应基准一个 CSS px；1440 宽页面重新排版，不使用 `transform: scale()` 整体缩小。

| 部位 | 固定值 | 说明 |
| --- | --- | --- |
| GlobalHeader | h=72, x=0,y=0,w=viewport | 下边线 1px，非另加高度 |
| Header Logo 区 | x=24,y=16,h=40 | 标记 36×36，字标 32px/38, weight700 |
| 项目切换 | x≈300,y=16,w=200,h=40 | 中等宽屏可收窄；2048 标尺禁止漂移 |
| 全局主导航 | x≈540,y=12,h=46 | 工作台/执行者/知识库，同一组件 |
| 命令搜索 | 2048 下 x≈1148,w=400,h=42 | Header 用 CSS grid 定位，不累计随意 margin |
| 右侧连接和用户区 | 剩余区域，min-w=350 | 1440 下隐藏连接文案，保留图标与 Tooltip |
| 管理 Sidebar | w=296，y=72,h=1080 | 六类管理页统一；原图 272–314 差异归一 |
| Task Sidebar | w=314，y=72,h=1080 | 执行、新任务、交付共用任务历史 |
| Execution 对话列 | w=602，x=314,y=72,h=1080 | 首次布局使用该值；用户调整后允许持久化 |
| Execution 右工作面 | x=916,w=1132 | 内边距 12–16；独立滚动 |
| 常规页面内边距 | 左右32，上28，下24 | 不给 root 限最大宽导致两侧空白漂移 |
| 面板分隔线 | 1px | 不与内容 padding 重复占位 |
| Resize handle | 交界线上 8px 命中宽 | 视觉线1px，中央 6×30 把手 |

Header 参考 grid：`268px 252px auto minmax(240px,400px)  auto`，左右24、gap16；在2048调校到上述锚点。全站同一 Header 文件，无页面覆盖它的尺寸。

root：`height:100dvh; display:grid; grid-template-rows:72px minmax(0,1fr); overflow:hidden`。每个主 pane 必须 `min-width:0; min-height:0`。不要把整个页面 body 和每个 panel 都变成可滚动容器。

## 3. 色板：分清浅紫描边与可读文字

| Token | 值 | 用途 |
| --- | --- | --- |
| `--af-bg` | #FCFBFE | 页面底色 |
| `--af-surface` | #FFFFFF | 内容面板 |
| `--af-surface-subtle` | #F8F5FD | 轻背景 |
| `--af-selected` | #F0EBFA | 当前菜单、选中行 |
| `--af-accent` | #A391D4 | 选中描边、连线、装饰状态 |
| `--af-accent-ink` | #6B5395 | 浅背景上的紫色文字 |
| `--af-accent-strong` | #795CAB | 强调填充、可见焦点 |
| `--af-text` | #24232A | 正文 |
| `--af-text-secondary` | #625E6D | 次要可读文字 |
| `--af-text-muted` | #797482 | 非关键说明，最终测对比度 |
| `--af-border` | #E7E3ED | 结构线 |
| `--af-button` | #30323A | 默认主要动作背景 |
| `--af-code` | #2D3137 | 差异背景 |
| `--af-success` | #238636 | 完成文字与图标 |
| `--af-success-bg` | #E8F7EB | 完成状态底 |
| `--af-attention` | #9A570D | 审批文字 |
| `--af-attention-bg` | #FFF5E5 | 审批轻背景 |
| `--af-danger` | #C24145 | 错误/拒绝/删除 |

不要用 `#A391D4` 画小号正文，它是描边/图形色。默认 primary 统一石墨灰填充白字；图 08/11 中紫色实心按钮归一为石墨灰，作为明确的跨页修正记录。不能让各页 Agent 另选紫色。

## 4. 字体与排版

本地提供 Noto Sans SC Variable WOFF2 和 Inter Variable WOFF2，代码字体 JetBrains Mono WOFF2；保留许可证。文件目标路径 `public/fonts/`。先通过浏览器确认中文、英文、数字和 `Codex CLI` 的实际 fallback；禁止依赖开发者机器是否装某个字体。若原始品牌字体补齐，整体替换并重新评审。

```css
@font-face {
  font-family: 'AF Sans';
  src: url('/fonts/noto-sans-sc-variable.woff2') format('woff2');
  font-weight: 100 900;
  font-display: swap;
}
@font-face {
  font-family: 'AF Latin';
  src: url('/fonts/inter-variable.woff2') format('woff2');
  font-weight: 100 900;
  font-display: swap;
}
@font-face {
  font-family: 'AF Mono';
  src: url('/fonts/jetbrains-mono.woff2') format('woff2');
  font-weight: 100 800;
  font-display: swap;
}
body { font-family: 'AF Latin', 'AF Sans', sans-serif; }
code, pre, .af-mono { font-family: 'AF Mono', monospace; }
```

| 层级 | 基准2048字号/行高/字重 |
| --- | --- |
| 管理页 H1 | 36/46/700 |
| 核心任务标题 | 28/38/650 |
| Panel 标题 | 22/30/650 |
| 正文/消息/表格主文字 | 18/28/400 |
| 操作按钮/Tab | 18/24/500 |
| 表单标签/元数据 | 16/24/500 与400 |
| 次级小字 | 14/20/400 |
| 代码 | 17/27/400 |
| 品牌字标 | 32/38/700 |

2048 的正文不是默认14px后台字号。1440以下正文16/24，页面标题28/38，次级信息14/20，不能全局按比例缩放。长任务名在列表单行省略并提供 Tooltip，消息、说明段落正常换行。按钮不换行；表格文本不能互相覆盖。

## 5. 组件规格

| 组件 | 规格与行为 |
| --- | --- |
| Button | 默认h44,padding-x18,radius8,gap8；sm h36；lg h52；主按钮石墨灰；disabled无点击 |
| IconButton | 40×40，图标20；aria-label必填；窄屏命中区至少44 |
| Input/Select | h46,radius8,border1,padding-x14；focus outline2、offset2；错误提示不覆盖输入 |
| Textarea | 新任务min-h144；composer min-h64,max-h220，超出内部滚动 |
| NavItem | h56,padding16,图标24,gap14；任务历史行h80；活动边条4px |
| Tabs | h44，trigger px20；浅紫底+下划线，两者同一选中状态 |
| StatusBadge | h30,px10,gap6,radius8,font14/20；icon16；图标与文案共同表达状态 |
| Panel | border1,radius10,bg white，默认无shadow；header h64 |
| Table | header h64；body row h64，padding-x24；border-collapse:collapse |
| FormSection | section gap24；label到field gap8；两列gap24；section间32 |
| ApprovalCard | border #E7C49A，white/pale背景，shield24，按钮非全卡片可点击 |
| Composer | 外框radius10,1px accent，padding14；工具栏36；总高约124 |
| GraphNode | 280×92（Planner可268×72），role18、harness16、status14，padding14 |
| FileRow | h48，图标20，右侧增删计数不参与文件名裁切 |

图标从同一套 lucide 组件导入，stroke-width=1.75，不能混用 emoji。第三方 Harness 图标需要正式本地资产；无法获得时用中性字母块，并在差异报告写明。AgentForge 自有标记建立一份 `BrandMark.vue`，全站复用，不让 Agent 自画12个版本。头像统一一份中性本地图标；原图人物头像不视为必须复制的真实用户资料。

## 6. 执行页精确锚点

下列是图03观察值，容差详见验收文件，不应作为整个页面绝对定位的理由。

| 区域 | x,y,w,h |
| --- | --- |
| Task Sidebar | 0,72,314,1080 |
| Conversation | 314,72,602,1080 |
| Conversation Header | 338,90,554,104 |
| UserBubble | 470,214,420,78 |
| Plan | 394,408,496,187 |
| Approval | 338,834,552,124 |
| Composer | 338,982,552,124 |
| Canvas Tabs | 916,72,1132,76 |
| Run Status Row | 930,148,1104,62 |
| Flow viewport | 930,210,1104,355 |
| Artifact toolbar | 930,579,1104,66 |
| File list | 936,664,290,314 |
| Code surface | 1240,664,794,314 |
| Artifact footer | 936,978,1098,64 |
| Collapsed run log | 936,1053,1098,56 |

会话使用 `grid-template-rows:auto minmax(0,1fr) auto`，计划/消息区域滚动，composer固定在会话列底部。禁止在真实长消息时覆盖审批或输入框。画布上半区高度固定基准355，下半区flex填充，不允许 `fitView` 因多次重新计算导致截图节点随机漂移。

## 7. 响应式与交互布局

| 宽度 | 行为 |
| --- | --- |
| ≥1920 | 使用基准列宽314+602+余量；管理侧栏296 |
| 1440–1919 | 任务侧栏240，会话min420，画布min640；Header隐藏连接长文案 |
| 1280–1439 | 任务侧栏可折叠72，会话420；画布余量；管理侧栏224；表格只在容器内横滚 |
| 1024–1279 | Sidebar收起为图标/Sheet；对话与执行面通过工作区Tab切换，不强塞三列 |
| <768 | Header56，侧栏Sheet；对话/画布/交付单视图；输入框保持可见；表单单列；表格转紧凑行 |

基准宽度下视图不能自动折叠。用户拖动后的宽度与参考模式分开存储；reference模式禁用用户持久化宽度，重置为契约值。

## 8. 动效

- Tabs 用 Animate UI 的共享选中指示器；删除与之重复的 CSS transform/height 动画，防止双重运动。
- Collapsible 用于计划、工具调用和运行记录；消息新内容不整段重复打字动画。
- Sheet 用于审批详情、移动导航；桌面审批主页面不强制弹Sheet。
- Pinned List 仅用于任务置顶；执行状态刷新不能重排用户正在看的行。
- Motion Grid 只用于活动状态，不成为整屏背景。
- `prefers-reduced-motion` 下关闭位移、路径流动和旋转，仍显示明确状态文字。
- `data-visual-test` 下关闭动画、光标、时钟变化，并等待字体和布局就绪。截图前不允许捕获弹簧半途。
