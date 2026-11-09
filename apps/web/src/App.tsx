import type { HealthResponse } from "@agentforge/contracts";
import { useQuery } from "@tanstack/react-query";
import { Activity, Database, Server, Workflow } from "lucide-react";

async function getHealth(): Promise<HealthResponse> {
  const response = await fetch("/api/health");
  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }
  return response.json() as Promise<HealthResponse>;
}

export function App() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 10_000 });
  const apiReady = health.data?.status === "ok";
  const databaseReady = health.data?.database === "ready";

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">AgentForge workspace</p>
          <h1>Control plane</h1>
        </div>
        <div className={`status ${apiReady ? "status-ready" : "status-pending"}`}>
          <Activity size={16} aria-hidden="true" />
          {health.isLoading ? "Checking" : apiReady ? "API online" : "API unavailable"}
        </div>
      </header>

      <section className="summary-grid" aria-label="Workspace status">
        <StatusItem icon={<Server />} label="API" value={apiReady ? "Ready" : "Unavailable"} />
        <StatusItem icon={<Database />} label="PostgreSQL" value={databaseReady ? "Ready" : "Unavailable"} />
        <StatusItem icon={<Workflow />} label="Runtime" value="Scaffolded" />
      </section>

      <section className="work-area">
        <div>
          <p className="section-label">Current phase</p>
          <h2>Development foundation</h2>
          <p>
            The workspace, control-plane health endpoint, and PostgreSQL runtime are ready for domain implementation.
          </p>
        </div>
        {health.error ? <pre className="error-message">{health.error.message}</pre> : null}
      </section>
    </main>
  );
}

function StatusItem({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <article className="status-item">
      <span className="status-icon">{icon}</span>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
      </div>
    </article>
  );
}
