/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ENABLE_REFERENCE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*?worker' {
  const workerConstructor: {
    new (): Worker
  }
  export default workerConstructor
}

