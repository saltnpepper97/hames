import { HamesApiError, updateSessionMode, getCurrentPlan } from "../../api/client";
import type { SessionMode } from "../../api/types";
import { ComposerMenu } from "../../chat/components/ComposerMenu";
import { ModelPicker } from "../../chat/components/ModelPicker";
import { Button } from "../../components/Button";
import { Icon } from "../../shell/icons";
import type { SemanticIconName } from "../../shell/icons";
import type {
  ComposerActionContribution,
  ComposerActionProps,
  ComposerControlContribution,
  ComposerControlProps,
} from "../../shell/plugins";

function mutationError(error: unknown): string {
  if (error instanceof HamesApiError) return error.message;
  return error instanceof Error ? error.message : "Hames could not update the session.";
}

function CommandControl(props: ComposerControlProps) {
  return (
    <Button
      variant="bare"
      class="composer-round commands"
      type="button"
      aria-label="Add to message"
      title="Add to message"
      disabled={props.disabled}
      onClick={props.onOpenActions}
    >
      <Icon name="action.add" size={18} />
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
      if (props.session.interaction_mode === "plan" && mode === "auto") {
        const plan = await getCurrentPlan(props.session.id);
        if (plan.current && ["ready", "failed"].includes(plan.current.status)) {
          props.onError("Use Execute plan above the composer to approve this plan, or Request changes to revise it.");
          return;
        }
      }
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
  { id: "hames.commands", seat: "left", order: 0, component: CommandControl },
  { id: "hames.mode", seat: "left", order: 10, component: ModeControl },
  { id: "hames.model", seat: "right", order: 0, component: ModelPicker },
] satisfies readonly ComposerControlContribution[];

function UploadAction(props: ComposerActionProps) {
  return (
    <Button variant="bare" role="menuitem" disabled={props.disabled} onClick={props.onUpload}>
      <span class="composer-action-icon"><Icon name="action.folderAdd" size={17} /></span>
      <span><strong>Upload files or images</strong><small>Images require a vision-capable model</small></span>
    </Button>
  );
}

function CommandsAction(props: ComposerActionProps) {
  return (
    <Button variant="bare" role="menuitem" disabled={props.disabled} onClick={props.onCommands}>
      <span class="composer-action-icon"><Icon name="conversation.context" size={17} /></span>
      <span><strong>Commands &amp; Skills</strong><small>Insert a slash command or invoke a Skill</small></span>
    </Button>
  );
}

export const coreComposerActions = [
  { id: "hames.upload", order: 0, component: UploadAction },
  { id: "hames.commands-and-skills", order: 10, component: CommandsAction },
] satisfies readonly ComposerActionContribution[];
