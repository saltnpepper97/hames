import { fireEvent, render, screen } from "@solidjs/testing-library";
import type { JSX } from "solid-js";
import { beforeEach, expect, it, vi } from "vitest";
import { listProviders, probeProvider } from "../../api/client";
import type { ProviderProfile, Session } from "../../api/types";
import { hamesIconPack } from "../../plugins/icons/hames";
import { IconProvider } from "../../shell/icons";
import { ModelPicker } from "./ModelPicker";

vi.mock("../../api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../api/client")>(),
  listProviders: vi.fn(),
  probeProvider: vi.fn(),
}));
vi.mock("@solidjs/router", () => ({
  A: (props: { href: string; children?: JSX.Element }) => <a href={props.href}>{props.children}</a>,
}));

const codex: ProviderProfile = {
  id: "codex", adapter: "codex", endpoint: "app-server://codex", configured_model: "",
  default_reasoning_effort: "medium", supported_reasoning_efforts: [],
};
const session: Session = {
  id: "test-chat", created_at: "2026-10-02T12:00:00Z", status: "open", title: null,
  working_directory: "/workspace", agent_id: "default", provider: "codex", model: "",
  reasoning_effort: "medium", interaction_mode: "manual", pinned: false,
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(listProviders).mockResolvedValue([codex]);
});

function openPicker() {
  render(() => <IconProvider pack={hamesIconPack}>
    <ModelPicker session={session} disabled={false} onSessionUpdated={vi.fn()} onError={vi.fn()} />
  </IconProvider>);
  fireEvent.click(screen.getByRole("button", { name: /Model and thinking:/ }));
  fireEvent.click(screen.getByRole("menuitem", { name: /Model\s*Choose model/ }));
}

it("shows the Codex connection failure when no models can be loaded", async () => {
  vi.mocked(probeProvider).mockResolvedValue({
    id: "codex", adapter: "codex", reachable: false, models: [],
    error: { code: "provider_not_configured", message: "Codex is not signed in", retryable: false, details: {} },
  });
  openPicker();
  expect(await screen.findByRole("alert")).toHaveTextContent("Codex / ChatGPT: Codex is not signed in");
});

it("keeps working models available while showing another provider's failure", async () => {
  vi.mocked(listProviders).mockResolvedValue([codex, { ...codex, id: "openai", adapter: "openai" }]);
  vi.mocked(probeProvider).mockImplementation(async (provider) => provider === "codex" ? {
    id: "codex", adapter: "codex", reachable: true, error: null,
    models: [{ id: "fixture-model", status: "available", context_length: 32000,
      parameter_size: null, quantization: null,
      input_modalities: ["text"], output_modalities: ["text"], reasoning_supported: false,
      reasoning_efforts: [] }],
  } : { id: "openai", adapter: "openai", reachable: false, models: [],
    error: { code: "provider_not_configured", message: "API key missing", retryable: false, details: {} } });
  openPicker();
  expect(await screen.findByRole("menuitemradio", { name: /fixture-model/ })).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("OpenAI API: API key missing");
});

it("clears a previous connection failure when reopening successfully", async () => {
  vi.mocked(probeProvider).mockRejectedValueOnce(new Error("Codex app-server exited"))
    .mockResolvedValue({ id: "codex", adapter: "codex", reachable: true, error: null, models: [] });
  openPicker();
  expect(await screen.findByRole("alert")).toHaveTextContent("Codex app-server exited");
  fireEvent.click(screen.getByRole("button", { name: /Model and thinking:/ }));
  fireEvent.click(screen.getByRole("button", { name: /Model and thinking:/ }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
