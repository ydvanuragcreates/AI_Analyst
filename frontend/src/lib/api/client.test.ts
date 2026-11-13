import { afterEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/types/api";

function mockFetchOnce(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    })
  );
}

describe("apiClient error mapping", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("maps the v1 error envelope ({error:{code,message}}) to ApiError", async () => {
    mockFetchOnce(404, { error: { code: "DATASET_NOT_FOUND", message: "Dataset 'x' was not found", request_id: "r1" } });

    await expect(apiClient.get("/api/v1/datasets/x")).rejects.toMatchObject({
      status: 404,
      code: "DATASET_NOT_FOUND",
      message: "Dataset 'x' was not found",
    });
  });

  it("maps the legacy error envelope ({detail}) to ApiError", async () => {
    mockFetchOnce(400, { detail: "Invalid SQL" });

    await expect(apiClient.get("/api/analysis/query")).rejects.toMatchObject({
      status: 400,
      message: "Invalid SQL",
    });
  });

  it("falls back to a generic message for an unrecognized error body", async () => {
    mockFetchOnce(500, {});

    const error = await apiClient.get("/api/v1/datasets").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toMatch(/went wrong/i);
  });

  it("never sends an Authorization header", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    await apiClient.get("/api/v1/datasets");

    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });
});
