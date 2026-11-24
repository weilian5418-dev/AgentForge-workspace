# 05 · 视觉与交互验收

## 1. “与设计图一样”如何落实

不能把生成PNG当成有完整设计token的Figma源文件。原图的文字字形、局部渐变、图标和坐标存在生成误差；系统字体、浏览器和Monaco渲染也会影响像素。验收分两层，并对修改留记录：

1. **设计还原层**：浏览器截图与原始PNG对照。检查大布局、锚点、文字、色彩、组件、状态和层级；PNG差异率作为诊断，不单独宣告合格。
2. **实现回归层**：经过人确认的浏览器截图作为baseline。以后每次代码变更以严格像素阈值和区域阈值比较，避免“本页修好了，全站变了”。

禁止首次截图自动成为“已批准”基线；禁止更新baseline掩盖布局偏移；禁止写“100%像素一致”却没有原图、实图、差异和人工结论。

## 2. 固定环境

- OS、Chromium版本、Node、依赖lockfile固定；CI与本地基线环境统一。Windows和Linux字体光栅可能不同，不能混着比较严格基线。
- 浏览器viewport 2048×1152、DPR1、缩放100%、locale zh-CN、timezone Asia/Shanghai、浅色、reducedMotion reduce。
- 明确reference构建，不连后端、不用真实时间、随机头像、随机ID、系统暗色和用户持久化分栏。
- 本地字体加载失败直接报失败；等待`document.fonts.ready`之外还要逐一`document.fonts.check`，否则fallback也可能被当ready。
- 禁用动画、caret、CSS transitions，Vue Flow初始布局只执行一次；Monaco等待首轮diff计算/布局完成后才发ready。
- 测试数据固定任务文案、分支、时间、数量、状态、代码内容和选中文件。
- screenshot不采用整页fullPage以掩盖溢出；参考图就是一个2048×1152视口。

页面ready定义：当前route的数据源已加载、字体成功、异步图/编辑器完成布局，下一帧完成后将 `document.documentElement.dataset.visualReady='true'`。路由变化先清空ready。API错误不能伪装ready主状态；另有error状态fixture。

## 3. Playwright配置

下面代码放目标Vue项目，不改现有React项目测试配置：

```ts
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  fullyParallel: false,
  use: {
    baseURL: 'http://127.0.0.1:5174',
    browserName: 'chromium',
    viewport: { width: 2048, height: 1152 },
    deviceScaleFactor: 1,
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    colorScheme: 'light',
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'pnpm dev --mode reference',
    url: 'http://127.0.0.1:5174',
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
```

`.env.reference`只有`VITE_ENABLE_REFERENCE=true`等非秘密开关；不把任何真实Key写进去。reference query参数和fixtureID来自manifest，不能从URL执行任意JS/请求任意外部地址。

截图测试骨架（需实现`reference`数据入口和data-ready契约后运行）：

```ts
import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const pages = [
  ['01-overview', '/workspace', 'overview'],
  ['02-new-task', '/tasks/new', 'newTask'],
  ['03-execution', '/tasks/demo-order-export', 'executing'],
  ['04-delivery-review', '/tasks/demo-order-export/delivery', 'deliveryReady'],
  ['05-task-library', '/tasks', 'taskLibrary'],
  ['06-inbox', '/inbox', 'approvalPending'],
  ['07-executors', '/executors', 'executors'],
  ['08-harness-profile', '/executors/profiles/demo-frontend', 'profile'],
  ['09-knowledge-library', '/knowledge', 'knowledge'],
  ['10-knowledge-document', '/knowledge/demo-api-contract', 'document'],
  ['11-extensions', '/extensions', 'extensions'],
  ['12-settings-system', '/settings/connection', 'settings'],
] as const

for (const [id, route, fixture] of pages) {
  test(id, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(`${route}?reference=1&fixture=${fixture}`)
    await page.evaluate(async () => { await document.fonts.ready })
    await expect(page.locator('html')).toHaveAttribute('data-visual-ready', 'true')
    await expect(page.locator(`[data-page-id="${id}"]`)).toBeVisible()
    const fontsLoaded = await page.evaluate(() =>
      document.fonts.check('18px "AF Sans"', '订单导出') &&
      document.fonts.check('18px "AF Latin"', 'AgentForge')
    )
    expect(fontsLoaded).toBe(true)
    expect(errors).toEqual([])
    await mkdir('visual/current', { recursive: true })
    await page.screenshot({ path: `visual/current/${id}.png`, animations: 'disabled', caret: 'hide', fullPage: false })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
    expect(overflow).toBe(false)
  })
}
```

字体检查应同时断言字体URL请求200（`fonts.check`对未知family的处理需谨慎），以及已加载FontFaceSet包含指定family；不允许无font-face声明时误过。进一步把代码字体检查加入03/04。

在视觉模式中增加：

```css
html[data-visual-test='true'] *,
html[data-visual-test='true'] *::before,
html[data-visual-test='true'] *::after {
  animation: none !important;
  transition: none !important;
  caret-color: transparent !important;
}
```

这不能停掉所有JS弹簧，所以provider reduced-motion与组件reference稳定状态必须同时生效。不能固定sleep两秒碰运气截图。

## 4. 原图比较工具

本规范附有 `scripts/compare-reference.mjs` 和 `regions.json`。安装sharp、pixelmatch后可运行：

```powershell
node D:\AgentFront\docs\vue-implementation\scripts\compare-reference.mjs --reference D:\AgentFront\output\imagegen\lavender-suite --actual D:\AgentFront\apps\workbench-vue\visual\current --out D:\AgentFront\apps\workbench-vue\visual\diff-design
```

输出每页diff、50%叠图、整体差异率、区域差异率、missing和size mismatch。默认诊断模式，不自动判原图还原成功。与已批准实现baseline比较时：

```powershell
node D:\AgentFront\docs\vue-implementation\scripts\compare-reference.mjs --reference D:\AgentFront\apps\workbench-vue\visual\baseline --actual D:\AgentFront\apps\workbench-vue\visual\current --out D:\AgentFront\apps\workbench-vue\visual\diff-regression --max-ratio 0.005 --max-region-ratio 0.01
```

整体阈值0.5%、单区域1%是同渲染环境初始回归门槛，按组件测量校准后可更严格。它不意味着阈值内的任何改动都可以接受。公共Header、侧栏、输入区等关键小区域分别校验，不能被一大片白底稀释。任何主按钮/关键文字缺失都人工否决。

原图比较不mask用户真正能看到的部分，不mask整块Monaco/Graph来获得低差异率。动态时间/头像应先在fixture固定。抗锯齿容忍只能处理渲染微差，不能容忍字体字号错一档。

## 5. 锚点与尺寸验收

建议将下面断言封装成boundingBox检查，比较region在页面坐标系的位置。目标值来自01文件；初始校准建议：

- Header高度72，误差≤1px；公共垂直分割线≤2px；主列宽≤3px。
- 主要面板四边≤4px；按钮高≤1px，radius按token；内部padding≤2px。
- 同等级字号使用唯一token，不能以截图“差不多”为由混用16/17/18。
- 文字换行与reference主状态一致；按钮不漏字、不被图标挤出、不覆盖。
- 选中状态、图节点角色、真实状态与计数一致；原图中错误代码/字形按纠正清单处理。

`data-region`至少包含header/sidebar/page-header/content；03额外conversation/canvas/graph/artifact/composer；06额外attention-list/approval-detail；10额外tree/article/document-meta。

## 6. 视觉评审顺序

按“外到内、结构到字”修复，避免先抠阴影后重做布局：

1. viewport、字体、Header、Sidebar、列边界。
2. 页面标题、面板尺寸、上下间距、滚动容器。
3. 文字字号、行高、字重、换行与截断。
4. Button/Tab/Badge/Input的尺寸和选中背景。
5. Graph节点、连线、diff文件rail和代码行。
6. 图标和边线、焦点、hover、微弱背景。

每次公共组件变更重截12页。只改某页正文时可先局部比对，再在最终合入前检查全套。不能为单页匹配在页面scoped CSS覆盖`.global-header`。

## 7. 自适应与行为验收

2048像素对应的还原，不等于小屏塞进同样3列。小屏按照01规则重排；分别截图1440×900、1280×800、1024×768、390×844。关键路径至少：

- 01创建草稿→02输入中文/分配→确认计划→03执行（reference模拟无外部副作用）。
- 03选择图节点/文件/终端/预览，调整分栏；新消息不抢滚动；暂停状态不假成功。
- 06批准/拒绝/过期/摘要变化；键盘焦点返回触发按钮。
- 04交付版本更新、要求修改、验收结果、附件下载。
- 07扫描pending/error/empty，08readonly和真实保存分开。
- 09检索无结果/处理中/部分可用，10长文档/长表格/非法HTML。
- 11 probe202/失败/版本冲突，12断线/重连/主题/reduced motion。

用Tab/Shift+Tab/Enter/Escape验证所有关键操作。Dialog/Sheet必须有title、focus trap、Escape关闭与focus恢复；图有语义列表，状态不只靠颜色；动态消息不要每个token都aria-live轰炸。

## 8. 交付报告模板

```text
实现版本/lockfile hash：
后端契约版本/hash：
参考图目录：
浏览器/OS/字体版本：
12页截图与diff目录：
共享几何变更：
原图纠正项：
通过的功能流程：
真实后端已联调项目：
未实现或能力不足：
未执行的检查：
人工确认的baseline：
```

文档方案本身不通过渲染finish gate；只有实际实现后截图、交互和状态检查才能记录pass。需要使用frontend-design-direction的evidence checker时，写真实本地report JSON，不伪造renderedEvidence或把PNG设计稿当浏览器实测证据。
