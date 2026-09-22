import { createSignal } from "solid-js";
import { fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IconProvider } from "../shell/icons";
import { hamesIconPack } from "../plugins/icons/hames";
import { Connections } from "./Connections";
import type { ProviderConnection } from "../api/client";

const api = vi.hoisted(() => ({ listConnections: vi.fn(), connectProvider: vi.fn(), testConnection: vi.fn(), disconnectProvider: vi.fn() }));
vi.mock("../api/client", () => api);
const row: ProviderConnection = { id: "deepseek", name: "DeepSeek", status: "not_connected", models: [], model_source: "", can_connect: true, can_disconnect: false, configured: false, source: "", key_url: "https://platform.deepseek.com/api_keys" };
beforeEach(() => { vi.resetAllMocks(); api.listConnections.mockResolvedValue([row]); });

describe("Connections", () => {
  it("verifies a hidden key and clears it after connecting", async () => {
    api.connectProvider.mockResolvedValue([{ ...row, status: "connected", models: ["deepseek-flash"], model_source: "discovered", can_disconnect: true, configured: true, source: "saved" }]);
    render(() => <IconProvider pack={hamesIconPack}><Connections /></IconProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
    const input = screen.getByLabelText("API key");
    expect(input).toHaveAttribute("type", "password");
    fireEvent.input(input, { target: { value: "private-fixture-key" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Connect" }).at(-1)!);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.connectProvider).toHaveBeenCalledWith("deepseek", "private-fixture-key");
    expect(screen.getByText("Connected · Key saved")).toBeInTheDocument();
    expect(screen.getByText("deepseek-flash")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Replace key" }));
    expect(screen.getByLabelText("API key")).toHaveValue("");
  });
  it.each(["mimo", "mimo_token_plan"])("connects %s using the hidden key flow", async (id) => {
    const mimo = { ...row, id, name: id === "mimo" ? "Xiaomi MiMo (API)" : "Xiaomi MiMo Token Plan", key_url: "https://platform.xiaomimimo.com/" };
    api.listConnections.mockResolvedValue([mimo]);
    api.connectProvider.mockResolvedValue([{ ...mimo, status: "connected", models: ["mimo-v2.6-pro"], model_source: "discovered", can_disconnect: true, configured: true, source: "saved" }]);
    render(() => <IconProvider pack={hamesIconPack}><Connections /></IconProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
    if (id === "mimo_token_plan") expect(screen.getByText(/dedicated Token Plan key/)).toBeInTheDocument();
    expect(screen.getByLabelText("API key")).toHaveAttribute("type", "password");
    fireEvent.input(screen.getByLabelText("API key"), { target: { value: "fixture-mimo-key" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Connect" }).at(-1)!);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.connectProvider).toHaveBeenCalledWith(id, "fixture-mimo-key");
    expect(screen.getByText("mimo-v2.6-pro")).toBeInTheDocument();
  });
  it("keeps a rejected key unconnected and allows correction", async () => {
    api.connectProvider.mockRejectedValue(new Error("Could not verify this key."));
    render(() => <IconProvider pack={hamesIconPack}><Connections /></IconProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
    fireEvent.input(screen.getByLabelText("API key"), { target: { value: "bad-key" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Connect" }).at(-1)!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not verify this key.");
    expect(screen.getByText("Not connected")).toBeInTheDocument();
  });
  it("checks configured connections automatically without probing disconnected providers", async () => {
    api.listConnections.mockResolvedValue([{ ...row, configured: true, status: "not_checked" }, { ...row, id: "zai", name: "Z.ai" }]);
    api.testConnection.mockResolvedValue([{ ...row, configured: true, status: "connected", models: ["deepseek-chat"] }]);
    render(() => <IconProvider pack={hamesIconPack}><Connections /></IconProvider>);
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(api.testConnection).toHaveBeenCalledExactlyOnceWith("deepseek");
    expect(screen.getByText("Not connected")).toBeInTheDocument();
  });
  it("waits for Web authentication before automatic checks", async () => {
    api.listConnections.mockResolvedValue([{ ...row, configured: true, status: "not_checked" }]);
    api.testConnection.mockResolvedValue([{ ...row, configured: true, status: "connected" }]);
    const [ready, setReady] = createSignal(false);
    render(() => <IconProvider pack={hamesIconPack}><Connections ready={ready()} /></IconProvider>);
    await screen.findByText("Not checked");
    expect(api.testConnection).not.toHaveBeenCalled();
    setReady(true);
    expect(await screen.findByText("Connected")).toBeInTheDocument();
  });
  it("renders a recoverable load failure", async () => {
    api.listConnections.mockRejectedValue(new Error("offline"));
    render(() => <IconProvider pack={hamesIconPack}><Connections /></IconProvider>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load connections.");
    api.listConnections.mockResolvedValue([row]);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("DeepSeek")).toBeInTheDocument();
  });
});
