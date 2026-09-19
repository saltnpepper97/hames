import { render } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { AgentAvatar } from "./AgentAvatar";

describe("AgentAvatar", () => {
  it("builds the cloud from evenly sized circular lobes", () => {
    const { container } = render(() => (
      <AgentAvatar
        name="Cloud"
        animated={false}
        config={{ shape: "cloud", eyes: "dots", face: "none", color: "#64748b" }}
      />
    ));

    const lobes = [...container.querySelectorAll(".agent-avatar-shell circle")];
    expect(lobes).toHaveLength(7);
    expect(new Set(lobes.map((lobe) => lobe.getAttribute("r")))).toEqual(new Set(["18"]));
    expect(container.querySelector(".agent-antenna")).toHaveAttribute(
      "d",
      "M40 10V5m0 0 5-4m-5 4-5-4",
    );
    expect(container.querySelector(".agent-avatar-eyes")).toHaveClass("on-shell");
    expect(container.querySelector(".agent-avatar-face")).not.toBeInTheDocument();
  });

  it("keeps the visor as dark glass with two light eye marks", () => {
    const { container } = render(() => (
      <AgentAvatar
        name="Visor"
        animated={false}
        config={{ shape: "circle", eyes: "visor", face: "solid", color: "#64748b" }}
      />
    ));

    expect(container.querySelector(".agent-visor")).toBeInTheDocument();
    expect(container.querySelectorAll(".agent-visor-eye")).toHaveLength(2);
    expect(container.querySelector(".agent-avatar-eyes")).not.toHaveClass("on-shell");
    expect(container.querySelector(".agent-avatar-face")).not.toBeInTheDocument();
  });

  it("never draws a face plate even when stored metadata still has one", () => {
    const { container } = render(() => (
      <AgentAvatar
        name="Legacy"
        animated={false}
        config={{ shape: "circle", eyes: "dots", face: "solid", color: "#ffffff" }}
      />
    ));

    expect(container.querySelector(".agent-avatar-face")).not.toBeInTheDocument();
    expect(container.querySelector(".agent-avatar-eyes")).toHaveClass("on-shell");
  });
});
