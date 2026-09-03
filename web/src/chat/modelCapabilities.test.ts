import { describe, expect, it } from "vitest";
import type { ProviderModel } from "../api/types";
import { modelReasoningEfforts } from "./modelCapabilities";

function model(overrides: Partial<ProviderModel> = {}): ProviderModel {
  return {
    id: "test-model",
    status: "available",
    context_length: null,
    parameter_size: null,
    quantization: null,
    reasoning_supported: true,
    reasoning_efforts: [],
    ...overrides,
  };
}

describe("model reasoning capabilities", () => {
  it("offers the binary choice for reasoning models without effort levels", () => {
    expect(modelReasoningEfforts(model())).toEqual(["on", "off"]);
    expect(modelReasoningEfforts(model({ reasoning_efforts: ["on"] }))).toEqual(["on", "off"]);
  });

  it("removes provider defaults and always offers off for level-based models", () => {
    expect(modelReasoningEfforts(model({
      reasoning_efforts: ["default", "low", "high"],
    }))).toEqual(["low", "high", "off"]);
  });

  it("does not invent thinking controls for unsupported models", () => {
    expect(modelReasoningEfforts(model({
      reasoning_supported: false,
      reasoning_efforts: ["low"],
    }))).toEqual([]);
  });
});
