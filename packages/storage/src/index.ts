import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";

export function createDatabasePool(connectionString: string) {
  return new Pool({ connectionString, max: 10 });
}

export function createDatabase(pool: Pool) {
  return drizzle(pool);
}

export async function checkDatabase(pool: Pool) {
  try {
    await pool.query("select 1");
    return true;
  } catch {
    return false;
  }
}
