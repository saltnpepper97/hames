import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { hamesIconPack } from "../plugins/icons/hames";
import { IconProvider } from "../shell/icons";
import { AgentAvatarEditor } from "./AgentAvatarEditor";

describe("AgentAvatarEditor", () => {
  it("offers visor eyes and does not expose a face plate control", () => {
    render(() => (
      <IconProvider pack={hamesIconPack}>
        <AgentAvatarEditor
          agentName="Navigator"
          initial={{ shape: "circle", eyes: "dots", face: "solid", color: "#64748b" }}
          saving={false}
          error=""
          onSave={() => {}}
          onClose={() => {}}
        />
      </IconProvider>
    ));

    expect(screen.getByText("Visor")).toBeInTheDocument();
    expect(screen.queryByText("Face plate")).not.toBeInTheDocument();
  });
});
