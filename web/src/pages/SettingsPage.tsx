import { SettingsSection } from "../components/SettingsSection";
import { Switch } from "../components/Switch";
import { useTheme } from "../theme/Theme";

export function SettingsPage() {
  const theme = useTheme();

  return (
    <section class="page settings-page" aria-labelledby="settings-title">
      <div class="page-heading settings-heading">
        <div>
          <span class="eyebrow">Local configuration</span>
          <h1 id="settings-title">Settings</h1>
          <p>Choose how the local Hames interface behaves and appears.</p>
        </div>
      </div>

      <div class="settings-page-stack">
        <SettingsSection
          title="Appearance"
          description="Appearance is stored only in this browser and applies across every Hames web surface."
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
      </div>
    </section>
  );
}
