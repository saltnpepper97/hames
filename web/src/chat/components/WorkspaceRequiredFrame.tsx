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
            </div>
          </div>
        </div>
        <div class="composer-dock" data-chat-region="composer">
          <div class="composer-workspace-control">
            <WorkspaceSwitcher
              workspaces={[]}
              adding={picking()}
              onSelect={async () => undefined}
              onAdd={() => void chooseWorkspace()}
            />
          </div>
          <div class="composer-shell pending-composer">
            <TextArea
              rows={1}
              resize="none"
              placeholder="Choose a workspace first"
              aria-label="Message Hames"
              disabled
            />
            <div class="composer-toolbar">
              <div class="composer-toolbar-spacer" />
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
      </ChatFrame>
      <Show when={picking()}>
        <WorkspaceAddDialog onClose={() => setPicking(false)} onAdded={openWorkspace} />
      </Show>
    </>
  );
}
