// @vitest-environment jsdom
//
// The login form shows the server's sentence for a rejected address, not
// "[object Object]" (item U). The 422 below is the backend's own body for
// someone@acme.test, captured from a bench stack, so the test runs the real
// api.localLogin against the shape FastAPI actually sends.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "./LoginPage";

const REASON =
  "value is not a valid email address: The part after the @-sign is a special-use or reserved name that cannot be used with email.";

const REJECTED_ADDRESS = {
  detail: [
    {
      type: "value_error",
      loc: ["body", "email"],
      msg: REASON,
      input: "someone@acme.test",
      ctx: { reason: "The part after the @-sign is a special-use or reserved name that cannot be used with email." },
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("LoginPage", () => {
  it("shows the validator's sentence when the address is rejected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(REJECTED_ADDRESS), { status: 422 })),
    );
    const { container } = render(<LoginPage onAuthenticated={() => {}} onWantInvite={() => {}} />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "someone@acme.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "x" } });
    fireEvent.submit(container.querySelector("form")!);

    const shown = await screen.findByText(REASON);
    expect(shown.className).toBe("login-error");
    expect(container.textContent).not.toContain("[object Object]");
  });
});
