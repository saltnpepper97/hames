import { HamesApiError, updateSessionMode } from "../../api/client";
import type { SessionMode } from "../../api/types";
import { ComposerMenu } from "../../chat/components/ComposerMenu";
import { ModelPicker } from "../../chat/components/ModelPicker";
import { Button } from "../../components/Button";
import { Icon } from "../../shell/icons";
import type { SemanticIconName } from "../../shell/icons";
import type { ComposerControlContribution, ComposerControlProps } from "../../shell/plugins";

function mutationError(error: unknown): string {
  if (error instanceof HamesApiError) return error.message;
  return error instanceof Error ? error.message : "Hames could not update the session.";
}

function AttachmentControl() {
  return (
    <Button
      variant="bare"
      class="composer-round attachment"
      type="button"
      aria-label="Add attachment"
      title="Attachments are coming soon"
      disabled
    >
      <Icon name="action.attach" size={18} />
    </Button>
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

export const coreComposerControls = [
  { id: "hames.attachments", seat: "left", order: 0, component: AttachmentControl },
  { id: "hames.mode", seat: "left", order: 10, component: ModeControl },
  { id: "hames.model", seat: "right", order: 0, component: ModelPicker },
] satisfies readonly ComposerControlContribution[];
