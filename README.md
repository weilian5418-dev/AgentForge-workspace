# AgentForge

AgentForge is a pnpm monorepo for a controlled multi-agent development workspace.

## Requirements

- Node.js 24+
- pnpm 12.18.0
- Docker Desktop with Linux containers

## Local Development

```bash
pnpm install
pnpm infra:up
pnpm dev
```

The web application runs on `http://localhost:5173`; the API listens on `http://localhost:3000`.

## Workspace

- `apps/web`: React and Vite frontend
- `apps/server`: Fastify control-plane API
- `apps/driver`: graph driver process
- `apps/worker`: agent worker process
- `apps/runner`: controlled execution service
- `apps/cli`: local lifecycle and doctor commands
- `packages/*`: shared contracts, storage, queue, and orchestration modules

