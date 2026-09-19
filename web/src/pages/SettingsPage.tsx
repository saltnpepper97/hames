import { useWorkspace } from "../shell/workspace";
import { Navigate } from "@solidjs/router";
import { Show, createEffect } from "solid-js";
import { UsageDashboard } from "../chat/components/UsageDialog";
import { SettingsSection } from "../components/SettingsSection";
import { Switch } from "../components/Switch";
import { Connections } from "../settings/Connections";
import { settingsCategory } from "../settings/categories";
import { useTheme } from "../theme/Theme";

interface SettingsPageProps {
  category?: string;
}

function SettingsContent(props: SettingsPageProps) {
  const theme = useTheme();
  const workspace = useWorkspace();

  createEffect(() => {
    const category = props.category ? settingsCategory(props.category)?.id : undefined;
    if (!category) return;
    queueMicrotask(() => {
      document.getElementById(`settings-${category}`)?.scrollIntoView?.({
        behavior: "smooth",
        block: "start",
      });
    });
  });

  return (
    <section class="page settings-page" aria-labelledby="settings-title">
      <div class="page-heading settings-heading">
        <div>
          <span class="eyebrow">Preferences and account</span>
          <h1 id="settings-title">Settings</h1>
          <p>Manage provider connections, appearance, and usage.</p>
        </div>
      </div>

      <div class="settings-page-stack">
        <Connections ready={workspace.connection() === "connected"} />
        <section
          id="settings-appearance"
          class="settings-page-section"
          aria-labelledby="settings-appearance-title"
        >
          <header class="settings-page-section-heading">
            <span class="eyebrow">Interface</span>
            <h2 id="settings-appearance-title">Appearance</h2>
            <p>Appearance is stored only in this browser and applies across every Hames web surface.</p>
          </header>
          <SettingsSection
            title="Theme"
            description="Choose the light or dark interface for this browser."
          >
            <Switch
              class="settings-switch"
              label="Dark mode"
              description="Use the neutral dark appearance."
              labelPosition="start"
              checked={theme.theme() === "dark"}
              onCheckedChange={(checked) => theme.setTheme(checked ? "dark" : "light")}
            />
          </SettingsSection>
        </section>

        <section
          id="settings-usage"
          class="settings-page-section"
          aria-labelledby="settings-usage-title"
        >
          <header class="settings-page-section-heading">
            <span class="eyebrow">Account and activity</span>
            <h2 id="settings-usage-title">Usage</h2>
            <p>Review ChatGPT and Grok account limits and pooled token activity from the past year.</p>
          </header>
          <UsageDashboard />
        </section>
      </div>
    </section>
  );
}

export function SettingsPage(props: SettingsPageProps) {
  return (
    <Show
      when={!props.category || settingsCategory(props.category)}
      fallback={<Navigate href="/settings" />}
    >
      <SettingsContent category={props.category} />
    </Show>
  );
}
