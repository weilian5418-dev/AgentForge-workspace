import { describe, expect, it } from "vitest";

import { healthResponseSchema } from "./index.js";

describe("healthResponseSchema", () => {
  it("accepts a ready health response", () => {
    const result = healthResponseSchema.parse({
      status: "ok",
      database: "ready",
      version: "0.1.0",
      timestamp: "2026-11-09T10:00:00.000Z",
    });

    expect(result.database).toBe("ready");
  });
});
