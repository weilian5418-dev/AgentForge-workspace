import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { usePreviewStore } from '../src/stores/preview'
import { useLibraryStore } from '../src/stores/library'
import { contentForFile } from '../src/features/artifacts/file-content'
import { createReferenceRepository } from '../src/data/repositories/reference'

beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', { getItem:(key:string)=>values.get(key) ?? null, setItem:(key:string,value:string)=>values.set(key,value) })
  setActivePinia(createPinia())
})

describe('frontend task workflow', () => {
  it('opens a newly created task with its own goal and role assignments', async () => {
    const preview = usePreviewStore()
    const roles = [{id:'author',label:'Frontend',harness:'Codex CLI',scope:'实现表单',status:'waiting' as const,x:0,y:0}]
    const id = preview.createTask('完成退款页面', roles)
    roles[0]!.scope = 'changed outside store'
    const repository = createReferenceRepository()
    const task = await repository.loadTask(id)
    expect(task.goal).toBe('完成退款页面')
    expect(task.roles[0]?.id).toBe('author')
    expect(preview.taskRoles[id]?.[0]?.scope).toBe('实现表单')
    expect((await repository.loadTasks('demo'))[0]?.id).toBe(id)
    preview.addMessage(id, '请补上空状态')
    expect((await repository.loadTask(id)).messages.at(-1)?.body).toBe('请补上空状态')
  })
  it('retains document edits and approval decisions when the local store is re-created', async () => {
    const local = useLibraryStore()
    local.documents.push({id:'local-spec',name:'退款规范.md',type:'Markdown',markdown:'# 退款规则',scope:'当前工作区',updated:'刚刚',ready:true})
    local.documentEdits['local-spec'] = '# 更新后的退款规则'
    local.decisions['demo-approval-export'] = 'allow_once'
    await nextTick()
    setActivePinia(createPinia())
    expect(useLibraryStore().decisions['demo-approval-export']).toBe('allow_once')
    const document = await createReferenceRepository().loadDocument('local-spec')
    expect(document.name).toBe('退款规范.md')
    expect(document.markdown).toBe('# 更新后的退款规则')
  })
  it('shows the selected file’s own source in the artifact inspector',()=> {
    const frontend=contentForFile({id:'export',name:'export.ts',language:'typescript',additions:42,deletions:0},{original:'',modified:'toolbar'})
    const tests=contentForFile({id:'tests',name:'test_export.py',language:'python',additions:24,deletions:0},{original:'',modified:'toolbar'})
    expect(frontend.modified).toContain('URLSearchParams')
    expect(tests.modified).toContain('test_export_requires_permission')
    expect(tests.modified).not.toBe(frontend.modified)
  })
  it('uses default library values when browser storage contains invalid JSON', () => {
    localStorage.setItem('af-local-library', '{invalid')
    expect(useLibraryStore().documents).toEqual([])
    expect(useLibraryStore().tools).toEqual([])
  })
})
