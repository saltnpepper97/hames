import { For, Show } from "solid-js";

const permissionCopy: Readonly<Record<string, { label: string; description: string; risk: string }>> = {
  "broker:project_read": {
    label: "Read project files",
    description: "Read and list files inside the active project through the Hames broker.",
    risk: "read",
  },
  "broker:project_write": {
    label: "Write project files",
    description: "Create or change files inside the active project through the Hames broker.",
    risk: "write",
  },
  "broker:process_run_scoped": {
    label: "Run scoped processes",
    description: "Start broker-controlled processes scoped to the active project.",
    risk: "execute",
  },
  "broker:network_request": {
    label: "Make network requests",
    description: "Send outbound requests through the broker. Network access is denied by default.",
    risk: "network",
  },
};

interface PluginPermissionListProps {
  permissions: string[];
  empty?: string;
}

export function PluginPermissionList(props: PluginPermissionListProps) {
  return (
    <Show
      when={props.permissions.length > 0}
      fallback={<p class="plugin-detail-empty">{props.empty ?? "This plugin requests no broker permissions."}</p>}
    >
      <ul class="plugin-permission-list">
        <For each={props.permissions}>{(permission) => {
          const copy = permissionCopy[permission] ?? {
            label: permission,
            description: "A permission declared by this plugin package.",
            risk: "unknown",
          };
          return (
            <li data-risk={copy.risk}>
              <span class="plugin-permission-mark" aria-hidden="true" />
              <div>
                <strong>{copy.label}</strong>
                <p>{copy.description}</p>
                <code>{permission}</code>
              </div>
            </li>
          );
        }}</For>
      </ul>
    </Show>
  );
}
