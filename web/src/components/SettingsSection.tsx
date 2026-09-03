import type { JSX, ParentProps } from "solid-js";

interface SettingsSectionProps extends ParentProps {
  title: string;
  description: string;
  action?: JSX.Element;
}

export function SettingsSection(props: SettingsSectionProps) {
  return (
    <section class="settings-section">
      <header class="settings-section-heading">
        <div>
          <h2>{props.title}</h2>
          <p>{props.description}</p>
        </div>
        {props.action}
      </header>
      <div class="settings-section-body">{props.children}</div>
    </section>
  );
}
