import { Show, createSignal } from "solid-js";
import { WorkspaceAddDialog } from "../../shell/WorkspaceAddDialog";
import type { Session, Workspace } from "../../api/types";
import { Button } from "../../components/Button";
import { TextArea } from "../../components/TextArea";
import { WorkspaceSwitcher } from "../../shell/WorkspaceSwitcher";
import { useWorkspace } from "../../shell/workspace";
import { Icon } from "../../shell/icons";
import { ChatFrame } from "./ChatFrame";
import { FreshChatHero } from "./FreshChatHero";

interface WorkspaceRequiredFrameProps {
  workspaceRequired?: boolean;
  notice?: string;
  error?: string;
  retryLabel?: string;
  onRetry?: () => void;
  busy?: boolean;
  canSelectWorkspace?: boolean;
  onSessionOpened: (session: Session) => void;
}

export function WorkspaceRequiredFrame(props: WorkspaceRequiredFrameProps) {
  const workspace = useWorkspace();
  const [picking, setPicking] = createSignal(false);

  const openWorkspace = async (selected: Workspace) => {
    await workspace.selectWorkspace(selected.id);
    props.onSessionOpened(await workspace.createChat());
  };

  const chooseWorkspace = () => setPicking(true);

  return (
    <>
      <ChatFrame fresh>
        <div class="chat-view-panel">
          <div class="transcript-scroll">
            <div class="transcript-column">
              <FreshChatHero />
              <Show when={props.error}>
                <div class="chat-start-notice" role="alert">
                  <p>{props.error}</p>
                  <Show when={props.onRetry}>
                    <Button variant="quiet" loading={props.busy} onClick={props.onRetry}>{props.retryLabel || "Try again"}</Button>
                  </Show>
                </div>
              </Show>
            </div>
          </div>
        </div>
        <div class="composer-dock" data-chat-region="composer">
          <div class="composer-workspace-control">
            <WorkspaceSwitcher
              workspaces={workspace.workspaces()}
              selected={workspace.selectedWorkspace()}
              disabled={props.canSelectWorkspace === false}
              adding={picking()}
              onSelect={async (id) => {
                await workspace.selectWorkspace(id);
                props.onSessionOpened(await workspace.createChat());
              }}
              onAdd={() => void chooseWorkspace()}
            />
          </div>
          <div class="composer-stack pending-composer">
            <div class="composer-shell">
              <TextArea
                rows={1}
                resize="none"
                placeholder="Message Hames…"
                aria-describedby="chat-start-guidance"
                aria-label="Message Hames"
                disabled
              />
              <div class="composer-toolbar">
                <p id="chat-start-guidance" class="composer-start-guidance" role="status">
                  {props.notice || (props.workspaceRequired !== false
                    ? "Select a workspace to start chatting."
                    : "Preparing your chat…")}
                </p>
                <Button
                  variant="bare"
                  class="composer-round send"
                  type="button"
                  aria-label="Send message"
                  disabled
                >
                  <Icon name="action.send" size={18} />
                </Button>
              </div>
            </div>
          </div>
        </div>
      </ChatFrame>
      <Show when={picking()}>
        <WorkspaceAddDialog onClose={() => setPicking(false)} onAdded={openWorkspace} />
      </Show>
    </>
  );
}
