import { healthResponseSchema } from "@agentforge/contracts";
import { checkDatabase, createDatabasePool } from "@agentforge/storage";
import Fastify from "fastify";

export function buildServer() {
  const app = Fastify({ logger: true });
  const databaseUrl = process.env.DATABASE_URL ?? "postgresql://agentforge:agentforge_dev@localhost:5432/agentforge";
  const pool = createDatabasePool(databaseUrl);

  app.get("/api/health", async () => {
    const database = (await checkDatabase(pool)) ? "ready" : "unavailable";
    return healthResponseSchema.parse({
      status: "ok",
      database,
      version: "0.1.0",
      timestamp: new Date().toISOString(),
    });
  });

  app.addHook("onClose", async () => {
    await pool.end();
  });

  return app;
}
