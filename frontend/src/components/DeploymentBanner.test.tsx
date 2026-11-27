// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { BANNER_ROLES, canSeeBanners } from "../lib/roles";
import type { DeploymentWarning } from "../types";
import { DeploymentBanner, deploymentBannerLines } from "./DeploymentBanner";

vi.mock("../api", () => ({ api: { getDeploymentWarnings: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function w(over: Partial<DeploymentWarning> & Pick<DeploymentWarning, "key">): DeploymentWarning {
  return { detail: "", ...over };
}

describe("deploymentBannerLines", () => {
  it("says nothing when nothing is wrong", () => {
    expect(deploymentBannerLines([])).toEqual([]);
  });

  it("writes one line per warning, saying who acts and where the page is", () => {
    const lines = deploymentBannerLines([
      w({ key: "storage_unreachable", detail: "failed: The storage bucket 'evidence' does not exist." }),
      w({ key: "storage_account", administers: true }),
      w({ key: "database_role", role: "wingrc", superuser: true }),
    ]);
    expect(lines).toEqual([
      "The app cannot use its object storage: The storage bucket 'evidence' does not exist. Uploads, " +
        'downloads and logos fail until it is fixed. Whoever runs this server should fix it. The operator\'s page is "Provisioning" in docs/deployment.md.',
      "The app reaches object storage with an account that administers it (MinIO's root, or an admin policy): " +
        "anyone holding the app's storage keys can make and remove accounts and read every bucket. Whoever runs " +
        'this server should fix it. The operator\'s page is "Moving a deployment off MinIO\'s root" in docs/deployment.md.',
      "The app connects to the database as wingrc, a superuser, so the database does not keep tenants apart; " +
        "only the application's checks do. Whoever runs this server should fix it. The operator's page is " +
        '"Database roles: which credential does what" in docs/deployment.md.',
    ]);
  });

  it("names what a storage account is granted beyond the app, or lacks", () => {
    expect(deploymentBannerLines([w({ key: "storage_account", beyond_the_app: ["s3:ListBucket on x"] })])[0]).toContain(
      "granted more than it uses: s3:ListBucket on x.",
    );
    expect(deploymentBannerLines([w({ key: "storage_account", missing: ["s3:DeleteObject"] })])[0]).toContain(
      "lacks s3:DeleteObject,",
    );
  });

  it("names BYPASSRLS and table ownership", () => {
    expect(deploymentBannerLines([w({ key: "database_role", role: "r", bypassrls: true })])[0]).toContain(
      "as r, which has BYPASSRLS,",
    );
    expect(deploymentBannerLines([w({ key: "database_role", role: "r", owner_of_rls_tables: 3 })])[0]).toContain(
      "as r, which owns 3 tables with row-level security,",
    );
  });
});

describe("DeploymentBanner", () => {
  it("renders nothing when the list is empty", async () => {
    vi.mocked(api.getDeploymentWarnings).mockResolvedValue({ warnings: [] });
    const { container } = render(<DeploymentBanner />);
    await Promise.resolve();
    expect(container.innerHTML).toBe("");
  });

  it("renders an alert, with no way to dismiss it", async () => {
    vi.mocked(api.getDeploymentWarnings).mockResolvedValue({
      warnings: [w({ key: "database_role", role: "wingrc", superuser: true })],
    });
    render(<DeploymentBanner />);
    expect((await screen.findByRole("alert")).textContent).toContain("does not keep tenants apart");
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("who sees the banners (CLAUDE.md, \"Banners\")", () => {
  it("is msp_admin and no other role, written out", () => {
    expect([...BANNER_ROLES]).toEqual(["msp_admin"]);
    expect(canSeeBanners("msp_admin")).toBe(true);
    for (const role of ["consultant_admin", "c3pao_assessor", "msp_engineer", "customer_poc", null]) {
      expect(canSeeBanners(role)).toBe(false);
    }
  });
});
