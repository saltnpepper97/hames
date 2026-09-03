import { HamesApiError, updateSessionMode, updateSessionReasoning } from "../../api/client";
import type { SessionMode } from "../../api/types";
import { ComposerMenu } from "../../chat/components/ComposerMenu";
import { Icon } from "../../shell/icons";
import type { SemanticIconName } from "../../shell/icons";
import type { ComposerControlContribution, ComposerControlProps } from "../../shell/plugins";

function mutationError(error: unknown): string {
  if (error instanceof HamesApiError) return error.message;
  return error instanceof Error ? error.message : "Hames could not update the session.";
}

function AttachmentControl() {
  return (
    <button
      class="composer-round attachment"
      type="button"
      aria-label="Add attachment"
      title="Attachments are coming soon"
      disabled
    >
      <Icon name="action.attach" size={18} />
    </button>
  );
}

function ModeControl(props: ComposerControlProps) {
  const icons: Record<SessionMode, SemanticIconName> = {
    auto: "mode.auto",
    plan: "mode.plan",
    manual: "mode.manual",
  };
  const select = async (mode: string) => {
    try {
      props.onSessionUpdated(await updateSessionMode(props.session.id, mode as SessionMode));
    } catch (error) {
      props.onError(mutationError(error));
      throw error;
    }
  };

  return (
    <ComposerMenu
      ariaLabel="Interaction mode"
      value={props.session.interaction_mode}
      icon={icons[props.session.interaction_mode]}
      disabled={props.disabled}
      options={[
        { value: "auto", label: "Auto", icon: "mode.auto" },
        { value: "plan", label: "Plan", icon: "mode.plan" },
        { value: "manual", label: "Manual", icon: "mode.manual" },
      ]}
      onSelect={select}
    />
  );
}

function ThinkingControl(props: ComposerControlProps) {
  const select = async (effort: string) => {
    try {
      props.onSessionUpdated(await updateSessionReasoning(props.session, effort));
    } catch (error) {
      props.onError(mutationError(error));
      throw error;
    }
  };

  const values = ["off", "low", "medium", "high", "xhigh"];
  const options = values.includes(props.session.reasoning_effort)
    ? values
    : [props.session.reasoning_effort, ...values];

  return (
    <ComposerMenu
      ariaLabel="Thinking level"
      value={props.session.reasoning_effort}
      icon="thinking.level"
      align="right"
      disabled={props.disabled}
      options={options.map((value) => ({
        value,
        label: value === "xhigh" ? "Extra high" : `${value[0]?.toUpperCase()}${value.slice(1)}`,
      }))}
      onSelect={select}
    />
  );
}

export const coreComposerControls = [
  { id: "hames.attachments", seat: "left", order: 0, component: AttachmentControl },
  { id: "hames.mode", seat: "left", order: 10, component: ModeControl },
  { id: "hames.thinking", seat: "right", order: 0, component: ThinkingControl },
] satisfies readonly ComposerControlContribution[];
