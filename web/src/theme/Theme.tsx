import { createContext, createEffect, createSignal, useContext } from "solid-js";
import type { ParentProps } from "solid-js";

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "hames.theme";

interface ThemeContextValue {
  theme: () => Theme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue>();

function initialTheme(): Theme {
  const applied = document.documentElement.dataset.theme;
  if (applied === "dark" || applied === "light") return applied;
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function ThemeProvider(props: ParentProps) {
  const [theme, setTheme] = createSignal<Theme>(initialTheme());

  createEffect(() => {
    const next = theme();
    document.documentElement.dataset.theme = next;
    document.documentElement.style.colorScheme = next;
    document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
      ?.setAttribute("content", next === "dark" ? "#18181b" : "#ffffff");
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Appearance still works when browser storage is unavailable.
    }
  });

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {props.children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("Theme context is unavailable");
  return context;
}
