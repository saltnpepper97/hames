import { deleteScar } from "../api/client";
import { DeletableSidebarRow } from "../components/DeletableSidebarRow";
import { A, useLocation, useNavigate } from "@solidjs/router";
import { For, Show, createEffect, createSignal } from "solid-js";
import type { Scar, ScarStatus } from "../api/types";
import { Button } from "../components/Button";
import { CollapsibleSidebarGroup } from "../components/CollapsibleSidebarGroup";
import { LoadingState } from "../components/LoadingState";
import { useSidebarSearch } from "../components/SidebarSearchContext";
import { Icon } from "../shell/icons";
import { useWorkspace } from "../shell/workspace";
import { ScarCreateDialog } from "./ScarCreateDialog";
import { useScarDirectory } from "./ScarDirectory";

const groups: readonly { label: string; statuses: readonly ScarStatus[] }[] = [
  { label: "Needs attention", statuses: ["candidate", "open", "repair_proposed", "regressed"] },
  { label: "Guarded", statuses: ["guarded"] },
  { label: "History", statuses: ["healed", "dismissed"] },
];

function groupScars(scars: Scar[], statuses: readonly ScarStatus[]): Scar[] {
  return scars.filter((scar) => statuses.includes(scar.status));
}

function label(value: string): string {
  return value.replaceAll("_", " ");
}

export function ScarSidebar() {
  const directory = useScarDirectory();
  const workspace = useWorkspace();
  const search = useSidebarSearch();
  const navigate = useNavigate();
  const location = useLocation();

  const matchingScars = () => {
    const query = search().trim().toLocaleLowerCase();
    if (!query) return directory.scars();
    return directory.scars().filter((scar) =>
      `${scar.title}\n${scar.description}\n${scar.failure_signature}\n${scar.status}\n${scar.severity}\n${scar.scope}`
        .toLocaleLowerCase()
        .includes(query)
    );
  };

  const visibleGroups = () => {
    if (!search().trim()) return groups;
    return groups.filter((group) => groupScars(matchingScars(), group.statuses).length > 0);
  };

  createEffect(() => {
    workspace.workingDirectory();
    void directory.ensureLoaded();
  });

  return (
    <div class="scar-sidebar-content">
      <Show when={directory.loading() && !directory.loaded()}>
        <LoadingState variant="sidebar" label="Loading Scars" />
      </Show>
      <Show when={directory.error()}>
        <div class="scar-sidebar-error" role="alert">
          <p>{directory.error()}</p>
          <Button size="small" onClick={() => void directory.refresh()}>Try again</Button>
        </div>
      </Show>
      <Show when={directory.loaded() && !directory.error()}>
        <nav class="scar-sidebar-list" aria-label="Scars">
          <For
            each={visibleGroups()}
            fallback={<p class="context-empty">No matching Scars.</p>}
          >{(group) => {
            const scars = () => groupScars(matchingScars(), group.statuses);
            return (
              <CollapsibleSidebarGroup
                label={group.label}
                meta={<span>{scars().length}</span>}
                class="scar-sidebar-group"
              >
                <For each={scars()} fallback={<p class="scar-group-empty">No Scars</p>}>
                  {(scar) => (
                    <DeletableSidebarRow name={scar.title} kind="Scar"
                      description={<p>This permanently removes the Scar and its repair records from active storage. Its audit history remains in the local ledger.</p>}
                      onDelete={async () => {
                        const sessionId = directory.sessionId();
                        if (!sessionId) throw new Error("No active session can delete this Scar.");
                        await deleteScar(sessionId, scar.id);
                        if (directory.sessionId() !== sessionId) return;
                        directory.remove(scar.id);
                        if (location.pathname === `/scars/${encodeURIComponent(scar.id)}`) navigate("/scars", { replace: true });
                      }}>
                      <A
                        href={`/scars/${encodeURIComponent(scar.id)}`}
                        class="scar-sidebar-item"
                        activeClass="active"
                        end
                      >
                        <span class="scar-sidebar-marker" data-severity={scar.severity} aria-hidden="true" />
                        <span class="scar-sidebar-copy">
                          <strong>{scar.title}</strong>
                          <span>{label(scar.status)} · {scar.severity}</span>
                        </span>
                      </A>
                    </DeletableSidebarRow>
                  )}
                </For>
              </CollapsibleSidebarGroup>
            );
          }}</For>
        </nav>
      </Show>
    </div>
  );
}

export function ScarSidebarAction() {
  const directory = useScarDirectory();
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const [sessionId, setSessionId] = createSignal("");
  const [preparing, setPreparing] = createSignal(false);
  const [error, setError] = createSignal("");

  const open = async () => {
    if (preparing()) return;
    setPreparing(true);
    setError("");
    try {
      await directory.ensureLoaded();
      const existing = directory.sessionId();
      setSessionId(existing || (await workspace.createChat()).id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to prepare Scar creation");
    } finally {
      setPreparing(false);
    }
  };

  const created = (scar: Scar) => {
    directory.add(scar);
    setSessionId("");
    navigate(`/scars/${encodeURIComponent(scar.id)}`);
  };

  return (
    <>
      <Button
        variant="bare"
        size="small"
        class="sidebar-context-action sidebar-create-action sidebar-icon-action"
        aria-label="Create Scar"
        loading={preparing()}
        disabled={workspace.connection() !== "connected"}
        title={error() || "Create Scar"}
        onClick={() => void open()}
      >
        <Icon name="action.add" size={15} />
      </Button>
      <Show when={sessionId()} keyed>
        {(selectedSessionId) => (
          <ScarCreateDialog
            sessionId={selectedSessionId}
            onClose={() => setSessionId("")}
            onCreated={created}
          />
        )}
      </Show>
    </>
  );
}
