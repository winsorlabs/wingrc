// @vitest-environment jsdom
//
// assetUrl() is the frontend half of evidence-download hardening
// (docs/roadmap.md): the backend returns app-relative paths for anything
// that streams through it (Evidence.download_url, system-description
// diagram urls) since it has no business knowing about the /api mount
// prefix nginx/Vite's dev proxy add -- this is what prepends it back on,
// and must leave an already-absolute URL (the org logo's presigned
// storage URL, the one remaining non-Evidence case) untouched.
import { describe, expect, it } from "vitest";
import { assetUrl } from "./api";

describe("assetUrl", () => {
  it("prepends the /api mount prefix to a bare backend route", () => {
    expect(assetUrl("/orgs/org1/evidence/ev1/download")).toBe(
      "/api/orgs/org1/evidence/ev1/download"
    );
  });

  it("leaves an absolute URL (presigned storage, e.g. the org logo) untouched", () => {
    const presigned = "https://minio.example.com/bucket/key?X-Amz-Signature=abc";
    expect(assetUrl(presigned)).toBe(presigned);
  });

  it("passes null through", () => {
    expect(assetUrl(null)).toBeNull();
  });
});
