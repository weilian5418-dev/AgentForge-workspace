import Fastify from "fastify";

const app = Fastify({ logger: true });
const port = Number(process.env.RUNNER_PORT ?? 3003);

app.get("/health", async () => ({ status: "ok", service: "runner" }));

try {
  await app.listen({ host: "127.0.0.1", port });
} catch (error) {
  app.log.error(error);
  process.exit(1);
}
