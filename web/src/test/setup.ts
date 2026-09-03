import "@testing-library/jest-dom/vitest";
import { cleanup } from "@solidjs/testing-library";
import { afterEach, vi } from "vitest";
import { resetClientForTests } from "../api/client";

Object.defineProperty(window, "scrollTo", { value: vi.fn(), writable: true });

const storageValues = new Map<string, string>();
const localStorageStub: Storage = {
  get length() { return storageValues.size; },
  clear: () => storageValues.clear(),
  getItem: (key) => storageValues.get(key) ?? null,
  key: (index) => [...storageValues.keys()][index] ?? null,
  removeItem: (key) => { storageValues.delete(key); },
  setItem: (key, value) => { storageValues.set(key, String(value)); },
};

Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: localStorageStub,
});

afterEach(() => {
  cleanup();
  resetClientForTests();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.style.removeProperty("color-scheme");
  vi.restoreAllMocks();
});
