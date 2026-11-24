import * as monaco from 'monaco-editor/editor/editor.api.js'
import 'monaco-editor/languages/definitions/typescript/register.js'
import 'monaco-editor/languages/definitions/javascript/register.js'
import 'monaco-editor/languages/definitions/python/register.js'
import 'monaco-editor/languages/definitions/html/register.js'
import EditorWorker from 'monaco-editor/editor/editor.worker.js?worker'

self.MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
}

export function mountDiff(
  container: HTMLElement,
  original: string,
  modified: string,
  language = 'typescript',
) {
  monaco.editor.defineTheme('agentforge-diff', {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#2D3137',
      'editor.lineHighlightBackground': '#34383F',
      'diffEditor.insertedTextBackground': '#23863630',
    },
  })

  const editor = monaco.editor.createDiffEditor(container, {
    readOnly: true,
    originalEditable: false,
    renderSideBySide: false,
    automaticLayout: true,
    minimap: { enabled: false },
    fontFamily: 'AF Mono',
    fontSize: 17,
    lineHeight: 27,
    scrollBeyondLastLine: false,
    theme: 'agentforge-diff',
    renderOverviewRuler: false,
    wordWrap: 'on',
    diffWordWrap: 'on',
    folding: false,
    links: false,
  })

  const originalModel = monaco.editor.createModel(original, language)
  const modifiedModel = monaco.editor.createModel(modified, language)
  editor.setModel({ original: originalModel, modified: modifiedModel })

  return () => {
    editor.dispose()
    originalModel.dispose()
    modifiedModel.dispose()
  }
}
