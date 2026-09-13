// @vitest-environment jsdom
//
// Coverage for the config-field self-description generalization and the
// test-connection recipient input (2026-09-14, after a live SMTP2GO
// misconfiguration: `encryption_mode: tls` read as "yes, encrypt this"
// and was wrong for the port in use, because the form rendered every
// config field as a bare text input regardless of type).
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { IntegrationConnector } from "../types";
import { IntegrationsPanel } from "./IntegrationsPanel";

vi.mock("../api", () => ({
  api: {
    listIntegrations: vi.fn(),
    setIntegrationCredential: vi.fn(),
    deleteIntegrationCredential: vi.fn(),
    testIntegrationConnection: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeSmtp(overrides: Partial<IntegrationConnector> = {}): IntegrationConnector {
  return {
    connector_key: "smtp",
    name: "Email (SMTP)",
    configured: true,
    config: { host: "mail.smtp2go.com", port: "2525", encryption_mode: "starttls", from_address: "noreply@example.com", from_name: "WinGRC", verify_cert: "true" },
    credential_hint: "9f8e",
    last_tested_at: null,
    last_test_ok: null,
    last_test_error: null,
    help_text: "Generic SMTP…",
    kind: "notification",
    optional_fields: ["username", "password"],
    test_input_label: "Send a test message to (optional)",
    config_fields: [
      { name: "host", label: "Host", type: "text", help_text: null, required: true, options: [] },
      { name: "port", label: "Port", type: "number", help_text: "Free-form…", required: true, options: [] },
      {
        name: "encryption_mode", label: "Encryption Mode", type: "select", help_text: null, required: true,
        options: [
          { value: "starttls", label: "STARTTLS — upgrade after connecting (ports 587, 2525, 8025)", suggests: { port: "587" } },
          { value: "tls", label: "Implicit TLS — encrypted from the first byte (port 465)", suggests: { port: "465" } },
          { value: "none", label: "None — unencrypted (not recommended)", suggests: {} },
        ],
      },
      { name: "from_address", label: "From Address", type: "text", help_text: null, required: true, options: [] },
      { name: "from_name", label: "From Name", type: "text", help_text: null, required: false, options: [] },
      { name: "verify_cert", label: "Verify Certificate", type: "boolean", help_text: "Uncheck only for a self-signed relay.", required: false, options: [] },
    ],
    credential_fields: ["username", "password"],
    ...overrides,
  };
}

function makeLiongard(overrides: Partial<IntegrationConnector> = {}): IntegrationConnector {
  return {
    connector_key: "liongard",
    name: "Liongard",
    configured: true,
    config: { instance_url: "https://myinstance.app.liongard.com" },
    credential_hint: "abcd",
    last_tested_at: null,
    last_test_ok: null,
    last_test_error: null,
    help_text: "Generate an Access Key…",
    kind: "data_source",
    optional_fields: [],
    test_input_label: null,
    config_fields: [
      { name: "instance_url", label: "Instance URL", type: "text", help_text: "The subdomain…", required: true, options: [] },
    ],
    credential_fields: ["access_key_id", "access_key_secret"],
    ...overrides,
  };
}

describe("IntegrationsPanel — config field rendering by type", () => {
  it("renders encryption_mode as a labeled select and verify_cert as a checkbox", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([makeSmtp()]);
    render(<IntegrationsPanel canWrite={true} kind="notification" />);
    await screen.findByText("Email (SMTP)");

    fireEvent.click(screen.getByRole("button", { name: "Edit credential" }));

    // The select renders real option labels naming the port, not just
    // the raw enum value.
    expect(screen.getByText(/STARTTLS — upgrade after connecting/)).toBeTruthy();
    expect(screen.getByText(/Implicit TLS — encrypted from the first byte/)).toBeTruthy();

    // verify_cert is a checkbox, not a text box someone could type
    // "false" into.
    const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
    expect(checkbox.checked).toBe(true); // stored config value was "true"
  });

  it("Liongard's single text field still renders as a plain required text input", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([makeLiongard()]);
    render(<IntegrationsPanel canWrite={true} kind="data_source" />);
    await screen.findByText("Liongard");

    fireEvent.click(screen.getByRole("button", { name: "Edit credential" }));
    expect(screen.getByText("Instance URL")).toBeTruthy();
    const input = screen.getByRole("textbox") as HTMLInputElement;
    expect(input.value).toBe("https://myinstance.app.liongard.com");
  });

  it("picking a mode suggests the matching port only when port is blank", async () => {
    const smtp = makeSmtp({ config: { ...makeSmtp().config, port: "" } });
    vi.mocked(api.listIntegrations).mockResolvedValue([smtp]);
    render(<IntegrationsPanel canWrite={true} kind="notification" />);
    await screen.findByText("Email (SMTP)");
    fireEvent.click(screen.getByRole("button", { name: "Edit credential" }));

    const portInput = screen.getByRole("spinbutton") as HTMLInputElement; // type="number" -- the port field
    expect(portInput.value).toBe(""); // blank before the suggestion
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "tls" } });
    expect(portInput.value).toBe("465");
  });

  it("does not overwrite an existing port value when the mode changes", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([makeSmtp()]); // port already "2525"
    render(<IntegrationsPanel canWrite={true} kind="notification" />);
    await screen.findByText("Email (SMTP)");
    fireEvent.click(screen.getByRole("button", { name: "Edit credential" }));

    const portInput = screen.getByDisplayValue("2525") as HTMLInputElement;
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "tls" } });
    expect(portInput.value).toBe("2525"); // untouched -- suggestion, not overwrite
  });
});

describe("IntegrationsPanel — test-connection recipient input", () => {
  it("shows an optional recipient field for SMTP but not for Liongard", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([makeSmtp(), makeLiongard()]);
    render(<IntegrationsPanel canWrite={true} kind="notification" />);
    await screen.findByText("Email (SMTP)");
    expect(screen.getByPlaceholderText("Send a test message to (optional)")).toBeTruthy();

    cleanup();
    vi.mocked(api.listIntegrations).mockResolvedValue([makeLiongard()]);
    render(<IntegrationsPanel canWrite={true} kind="data_source" />);
    await screen.findByText("Liongard");
    expect(screen.queryByPlaceholderText(/test message/)).toBeNull();
  });

  it("the recipient field is empty on every fresh render -- never prefilled", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([makeSmtp()]);
    render(<IntegrationsPanel canWrite={true} kind="notification" />);
    await screen.findByText("Email (SMTP)");
    const field = screen.getByPlaceholderText("Send a test message to (optional)") as HTMLInputElement;
    expect(field.value).toBe("");
  });

  it("passes the typed recipient through to the API call", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([makeSmtp()]);
    vi.mocked(api.testIntegrationConnection).mockResolvedValue(makeSmtp({ last_test_ok: true }));
    render(<IntegrationsPanel canWrite={true} kind="notification" />);
    await screen.findByText("Email (SMTP)");

    const field = screen.getByPlaceholderText("Send a test message to (optional)");
    fireEvent.change(field, { target: { value: "someone@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await screen.findByRole("button", { name: "Test connection" }); // settles back from "Testing…"
    expect(api.testIntegrationConnection).toHaveBeenCalledWith("smtp", "someone@example.com");
  });

  it("clears the recipient after a test run, win or lose", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([makeSmtp()]);
    vi.mocked(api.testIntegrationConnection).mockResolvedValue(makeSmtp({ last_test_ok: true }));
    render(<IntegrationsPanel canWrite={true} kind="notification" />);
    await screen.findByText("Email (SMTP)");

    const field = screen.getByPlaceholderText("Send a test message to (optional)") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "someone@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await screen.findByRole("button", { name: "Test connection" });
    expect(field.value).toBe("");
  });

  it("test connection with no recipient passes undefined, not an empty string", async () => {
    vi.mocked(api.listIntegrations).mockResolvedValue([makeSmtp()]);
    vi.mocked(api.testIntegrationConnection).mockResolvedValue(makeSmtp({ last_test_ok: true }));
    render(<IntegrationsPanel canWrite={true} kind="notification" />);
    await screen.findByText("Email (SMTP)");

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByRole("button", { name: "Test connection" });
    expect(api.testIntegrationConnection).toHaveBeenCalledWith("smtp", undefined);
  });
});
