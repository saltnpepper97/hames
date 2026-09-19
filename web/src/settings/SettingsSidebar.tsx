import { A, useLocation } from "@solidjs/router";
import { For } from "solid-js";
import { settingsCategories } from "./categories";

export function SettingsSidebar() {
  const location = useLocation();
  return (
    <nav class="settings-sidebar-list" aria-label="Settings categories">
      <For each={settingsCategories}>
        {(category) => (
          <A
            href={`/settings/${encodeURIComponent(category.id)}`}
            class="settings-sidebar-item"
            classList={{ active: location.pathname === "/settings" && category.id === "appearance" }}
            activeClass="active"
            end
          >
            <strong>{category.label}</strong>
            <span>{category.description}</span>
          </A>
        )}
      </For>
    </nav>
  );
}
