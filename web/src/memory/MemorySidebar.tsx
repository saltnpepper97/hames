import { deleteMemory } from "../api/client";
import { DeletableSidebarRow } from "../components/DeletableSidebarRow";
import { A, useLocation, useNavigate } from "@solidjs/router";
import { For, Show, createEffect, createSignal } from "solid-js";
import type { MemoryLayer, MemoryRecord } from "../api/types";
import { Button } from "../components/Button";
import { CollapsibleSidebarGroup } from "../components/CollapsibleSidebarGroup";
import { LoadingState } from "../components/LoadingState";
import { useSidebarSearch } from "../components/SidebarSearchContext";
import { Icon } from "../shell/icons";
import { useWorkspace } from "../shell/workspace";
import { useMemoryDirectory } from "./MemoryDirectory";
import { MemoryCreateDialog } from "./MemoryCreateDialog";

const groups: readonly { layer: MemoryLayer; label: string }[] = [
  { layer: "relationship", label: "Relationships" },
  { layer: "semantic", label: "Semantic" },
  { layer: "episodic", label: "Episodes" },
];

function visibilityLabel(value: string): string {
  return value.replaceAll("_", " ");
}

export function MemorySidebar() {
  const directory = useMemoryDirectory();
  const workspace = useWorkspace();
  const search = useSidebarSearch();
  const navigate = useNavigate();
  const location = useLocation();

  const matchingRecords = () => {
    const query = search().trim().toLocaleLowerCase();
    if (!query) return directory.records();
    return directory.records().filter((record) =>
      `${record.summary}\n${record.subject}\n${record.predicate}\n${record.layer}\n${record.visibility}`
        .toLocaleLowerCase()
        .includes(query)
    );
  };

  const visibleGroups = () => {
    if (!search().trim()) return groups;
    return groups.filter((group) =>
      matchingRecords().some((record) => record.layer === group.layer)
    );
  };

  createEffect(() => {
    workspace.workingDirectory();
    void directory.ensureLoaded();
  });

  return (
    <div class="memory-sidebar-content">
      <Show when={directory.loading() && !directory.loaded()}>
        <LoadingState variant="sidebar" label="Loading memory" />
      </Show>
      <Show when={directory.error()}>
        <div class="memory-sidebar-error" role="alert">
          <p>{directory.error()}</p>
          <Button size="small" onClick={() => void directory.refresh()}>Try again</Button>
        </div>
      </Show>
      <Show when={directory.loaded() && !directory.error()}>
        <nav class="memory-sidebar-list" aria-label="Memories">
          <For
            each={visibleGroups()}
            fallback={<p class="context-empty">No matching memories.</p>}
          >{(group) => {
            const records = () => matchingRecords().filter((record) => record.layer === group.layer);
            return (
              <CollapsibleSidebarGroup
                  label={group.label}
                  meta={<span>{records().length}</span>}
                  class="memory-sidebar-group"
              >
                <For each={records()} fallback={<p class="memory-group-empty">No active records</p>}>
                  {(record) => (
                    <DeletableSidebarRow name={record.summary} kind="memory"
                      description={<p>This permanently removes the memory from Hames retrieval. The audit event recording this deletion remains in the local ledger.</p>}
                      onDelete={async () => {
                        const sessionId = directory.sessionId();
                        if (!sessionId) throw new Error("No active session can delete this memory.");
                        await deleteMemory(sessionId, record.id);
                        if (directory.sessionId() !== sessionId) return;
                        directory.remove(record.id);
                        if (location.pathname === `/memory/${encodeURIComponent(record.id)}`) navigate("/memory", { replace: true });
                      }}>
                      <A
                        href={`/memory/${encodeURIComponent(record.id)}`}
                        class="memory-sidebar-item"
                        activeClass="active"
                        end
                      >
                        <strong>{record.summary}</strong>
                        <span>{visibilityLabel(record.visibility)}</span>
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

export function MemorySidebarAction() {
  const directory = useMemoryDirectory();
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
      setError(caught instanceof Error ? caught.message : "Unable to prepare memory creation");
    } finally {
      setPreparing(false);
    }
  };

  const created = (record: MemoryRecord) => {
    directory.add(record);
    setSessionId("");
    navigate(`/memory/${encodeURIComponent(record.id)}`);
  };

  return (
    <>
      <Button
        variant="bare"
        size="small"
        class="sidebar-context-action sidebar-create-action sidebar-icon-action"
        aria-label="Add Memory"
        loading={preparing()}
        disabled={workspace.connection() !== "connected"}
        title={error() || "Add memory"}
        onClick={() => void open()}
      >
        <Icon name="action.add" size={15} />
      </Button>
      <Show when={sessionId()} keyed>
        {(selectedSessionId) => (
          <MemoryCreateDialog
            sessionId={selectedSessionId}
            onClose={() => setSessionId("")}
            onCreated={created}
          />
        )}
      </Show>
    </>
  );
}
