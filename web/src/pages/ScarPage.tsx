import { useNavigate } from "@solidjs/router";
import { For, Show, createEffect, createSignal, onCleanup } from "solid-js";
import { deleteScar, inspectScar } from "../api/client";
import type { Scar, ScarInspection } from "../api/types";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { DeleteConfirmationDialog } from "../components/DeleteConfirmationDialog";
import { LoadingState } from "../components/LoadingState";
import { DetailHeading } from "../components/DetailHeading";
import { DetailStatStrip } from "../components/DetailStatStrip";
import { Separator } from "../components/Separator";
import { useScarDirectory } from "../scars/ScarDirectory";
import { ScarDiagnosis } from "../scars/ScarDiagnosis";
import { ScarEvidenceTimeline } from "../scars/ScarEvidenceTimeline";
import { ScarLifecycle } from "../scars/ScarLifecycle";
import { ScarRepairCard } from "../scars/ScarRepairCard";
import { ScarStatusBadge } from "../scars/ScarStatusBadge";
import { ScarTriggers } from "../scars/ScarTriggers";
import { scarDate, scarLabel, shortScarId } from "../scars/format";
import { Icon } from "../shell/icons";

interface ScarPageProps {
  scarId: string;
}

function protectionCopy(scar: ScarInspection): string {
  switch (scar.status) {
    case "candidate": return "Detected and awaiting human confirmation.";
    case "open": return "Confirmed, but no active repair protects future work yet.";
    case "repair_proposed": return "A repair has been proposed and is moving through evaluation.";
    case "guarded": return "A promoted repair is protecting matching future work.";
    case "healed": return "The repair held across comparable runs; regression checks remain active.";
    case "regressed": return "The failure returned after protection. This Scar needs another repair pass.";
    case "dismissed": return "Dismissed from active repair while its audit history remains intact.";
  }
}

function ScarDetail(props: { scar: Scar; inspection: ScarInspection; onDelete: () => void }) {
  const repair = () => props.inspection.repair_layer
    ? scarLabel(props.inspection.repair_layer)
    : "No repair attached";
  return (
    <section class="page scar-page" aria-labelledby="scar-title">
      <DetailHeading
        id="scar-title"
        class="scar-heading"
        eyebrow={<>Scar · {shortScarId(props.scar.id)}</>}
        title={scarLabel(props.inspection.detection)}
        summary={props.inspection.title}
        context={<p>{protectionCopy(props.inspection)}</p>}
        badges={<div class="scar-heading-badges" aria-label="Scar classification">
          <ScarStatusBadge status={props.inspection.status} />
          <ScarStatusBadge severity={props.inspection.severity} />
          <Badge variant="outline" size="sm">{scarLabel(props.inspection.scope)}</Badge>
          <Button
            variant="icon"
            class="detail-delete-action"
            aria-label={`Delete ${props.inspection.title}`}
            title="Delete Scar"
            onClick={props.onDelete}
          >
            <Icon name="action.delete" size={17} />
          </Button>
        </div>}
      />

      <DetailStatStrip
        label="Scar protection status"
        items={[
          { label: "Evidence", value: props.inspection.evidence_timeline.length },
          { label: "Clean guards", value: props.inspection.successful_guard_count },
          { label: "Regressions", value: props.inspection.regression_count },
          { label: "Repair layer", value: repair() },
        ]}
      />

      <ScarDiagnosis inspection={props.inspection} />

      <Separator label="Protection" />
      <div class="scar-protection-grid">
        <section class="scar-signature-panel">
          <span class="eyebrow">Failure signature</span>
          <code>{props.inspection.failure_signature}</code>
        </section>
        <section class="scar-trigger-panel">
          <div>
            <span class="eyebrow">Trigger conditions</span>
            <p>These conditions narrow when Hames should recognize or guard against this failure.</p>
          </div>
          <ScarTriggers trigger={props.inspection.trigger} />
        </section>
      </div>

      <Separator label="Repair history" />
      <div class="scar-repair-list">
        <For each={props.inspection.repairs} fallback={
          <div class="scar-empty-panel">
            <strong>No repair has been attached.</strong>
            <p>The Scar remains visible so a future repair can stay grounded in its original evidence.</p>
          </div>
        }>
          {(item) => <ScarRepairCard repair={item} evaluations={props.inspection.evaluations} />}
        </For>
      </div>

      <div class="scar-audit-grid">
        <section>
          <Separator label="Lifecycle" />
          <ScarLifecycle transitions={props.inspection.transitions} />
        </section>
        <section>
          <Separator label="Evidence" />
          <ScarEvidenceTimeline evidence={props.inspection.evidence_timeline} />
        </section>
      </div>

      <Separator label="Record" />
      <dl class="scar-record-metadata">
        <div><dt>Created</dt><dd>{scarDate(props.inspection.created_at)}</dd></div>
        <div><dt>Updated</dt><dd>{scarDate(props.inspection.updated_at)}</dd></div>
        <div><dt>Last triggered</dt><dd>{scarDate(props.scar.last_triggered_at)}</dd></div>
        <div><dt>Source session</dt><dd><code>{shortScarId(props.scar.source_session_id)}</code></dd></div>
        <div><dt>Source run</dt><dd>{props.scar.source_run_id ? <code>{shortScarId(props.scar.source_run_id)}</code> : "None"}</dd></div>
        <div><dt>Owner</dt><dd>{props.scar.owner_agent_id ?? "Shared"}</dd></div>
      </dl>
    </section>
  );
}

export function ScarPage(props: ScarPageProps) {
  const directory = useScarDirectory();
  const navigate = useNavigate();
  const [inspection, setInspection] = createSignal<ScarInspection>();
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [confirmingDelete, setConfirmingDelete] = createSignal(false);
  let requestId = 0;

  const scar = () => directory.scars().find((candidate) => candidate.id === props.scarId);
  const load = async (sessionId: string, scarId: string, currentRequest: number) => {
    setLoading(true);
    setError("");
    try {
      const next = await inspectScar(sessionId, scarId);
      if (currentRequest === requestId) setInspection(next);
    } catch (caught) {
      if (currentRequest === requestId) {
        setInspection(undefined);
        setError(caught instanceof Error ? caught.message : "Unable to inspect this Scar");
      }
    } finally {
      if (currentRequest === requestId) setLoading(false);
    }
  };

  createEffect(() => {
    const sessionId = directory.sessionId();
    const scarId = props.scarId;
    if (!sessionId || !scarId) return;
    setInspection(undefined);
    const currentRequest = ++requestId;
    void load(sessionId, scarId, currentRequest);
  });
  onCleanup(() => { requestId += 1; });

  const remove = async () => {
    const sessionId = directory.sessionId();
    if (!sessionId) throw new Error("No active session can delete this Scar.");
    await deleteScar(sessionId, props.scarId);
    directory.remove(props.scarId);
    setConfirmingDelete(false);
    navigate("/scars", { replace: true });
  };

  return (
    <>
    <Show
      when={scar()}
      keyed
      fallback={
        <section class="page scar-route-state">
          <Show when={directory.loading() || !directory.loaded()}>
            <LoadingState variant="detail" label="Loading Scar" />
          </Show>
          <Show when={directory.loaded() && !directory.loading()}>
            <div class="error-state">
              <div><span class="eyebrow">Scar</span><h2>This Scar is not available.</h2><p>It may no longer be visible from this workspace.</p></div>
            </div>
          </Show>
        </section>
      }
    >
      {(selected) => (
        <Show when={inspection()} keyed fallback={
          <section class="page scar-route-state">
            <Show when={loading()}><LoadingState variant="detail" label="Inspecting Scar" /></Show>
            <Show when={error()}>
              <div class="error-state">
                <div><span class="eyebrow">Scar inspection</span><h2>The lineage could not be loaded.</h2><p>{error()}</p></div>
                <Button onClick={() => {
                  const sessionId = directory.sessionId();
                  if (sessionId) void load(sessionId, props.scarId, ++requestId);
                }}>Try again</Button>
              </div>
            </Show>
          </section>
        }>
          {(details) => <ScarDetail scar={selected} inspection={details} onDelete={() => setConfirmingDelete(true)} />}
        </Show>
      )}
    </Show>
    <Show when={confirmingDelete() && inspection()} keyed>
      {(details) => (
        <DeleteConfirmationDialog
          eyebrow="Delete Scar"
          title={`Delete ${details.title}?`}
          confirmLabel="Delete Scar"
          onClose={() => setConfirmingDelete(false)}
          onConfirm={remove}
        >
          <p>This permanently removes the Scar and its repair records from active storage.</p>
          <p>The audit event recording this deletion remains in the local ledger.</p>
        </DeleteConfirmationDialog>
      )}
    </Show>
    </>
  );
}
