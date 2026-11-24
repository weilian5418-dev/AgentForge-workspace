import { onBeforeUnmount, watch, type Ref } from 'vue'
import { useRoute } from 'vue-router'

async function waitFonts() {
  await document.fonts.ready
  const sansOk = document.fonts.check('18px "AF Sans"', '订单导出')
  const latinOk = document.fonts.check('18px "AF Latin"', 'AgentForge')
  return sansOk && latinOk
}

export function useVisualReady(ready: Ref<boolean>) {
  const route = useRoute()

  async function markReady() {
    delete document.documentElement.dataset.visualReady
    if (!ready.value) return
    const fontsOk = await waitFonts()
    if (!fontsOk) return
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    document.documentElement.dataset.visualReady = 'true'
    if (route.query.fixture) {
      document.documentElement.dataset.visualTest = 'true'
    } else delete document.documentElement.dataset.visualTest
  }

  const stop = watch(ready, () => { void markReady() }, { immediate: true })
  onBeforeUnmount(() => {
    stop()
    delete document.documentElement.dataset.visualReady
  })
}
