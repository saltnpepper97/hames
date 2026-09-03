import { Button } from "../../components/Button";
import { Icon } from "../../shell/icons";
import { ChatFrame } from "./ChatFrame";
import { FreshChatHero } from "./FreshChatHero";

export function PendingChatFrame() {
  return (
    <ChatFrame fresh>
      <div class="transcript-scroll" aria-busy="true">
        <div class="transcript-column">
          <FreshChatHero pending />
        </div>
      </div>
      <div class="composer-dock" data-chat-region="composer">
        <div class="composer-shell pending-composer" aria-busy="true">
          <textarea
            rows={1}
            placeholder="Message Hames"
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
  );
}
