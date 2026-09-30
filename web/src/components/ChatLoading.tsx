import { Icon } from "../shell/icons";
import { Spinner } from "./Spinner";

export function ChatLoading() {
  return (
    <div class="chat-loading" role="status" aria-label="Loading chat" aria-busy="true">
      <div class="chat-loading-content" aria-hidden="true">
        <div class="chat-loading-mark">
          <Icon name="brand.mark" size={36} />
        </div>
        <span class="chat-loading-title">Getting things ready</span>
        <div class="chat-loading-caption">
          <Spinner size="sm" />
          <span>Opening your workspace</span>
        </div>
      </div>
    </div>
  );
}
