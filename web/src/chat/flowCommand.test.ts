import { expect, it, vi } from "vitest";
import { parseWebCommand } from "./webCommands";
import { executeWebCommand } from "./commandExecution";
import { startFlow } from "../api/client";
vi.mock("../api/client", () => ({ startFlow: vi.fn().mockResolvedValue({ id: "run" }) }));
it("starts any saved flow in the existing conversation without opening another chat", async () => {
  const command = parseWebCommand("/flow research-report Research accessible navigation\nUse primary sources");
  expect(command).toEqual({ kind: "flow", id: "research-report", input: "Research accessible navigation\nUse primary sources", usePlan: false });
  const result = await executeWebCommand(command!, "existing-chat");
  expect(startFlow).toHaveBeenCalledWith("research-report", "existing-chat", "Research accessible navigation\nUse primary sources", false);
  expect(result.openedSession).toBeUndefined();
  const planned = parseWebCommand("/flow build-review --plan Preserve the API")!;
  await executeWebCommand(planned, "existing-chat");
  expect(startFlow).toHaveBeenLastCalledWith("build-review", "existing-chat", "Preserve the API", true);
  await expect(executeWebCommand(parseWebCommand("/flow")!, "existing-chat")).rejects.toThrow("Use /flow");
});
