import "@testing-library/jest-dom/vitest";
import { cleanup } from "@solidjs/testing-library";
import { afterEach, vi } from "vitest";
import { resetClientForTests } from "../api/client";

Object.defineProperty(window, "scrollTo", { value: vi.fn(), writable: true });

afterEach(() => {
  cleanup();
  resetClientForTests();
  vi.restoreAllMocks();
});
