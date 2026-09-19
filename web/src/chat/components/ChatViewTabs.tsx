import { Button } from "../../components/Button";

export type ChatView = "chat" | "events";

interface ChatViewTabsProps {
  value: ChatView;
  onChange: (view: ChatView) => void;
}

export function ChatViewTabs(props: ChatViewTabsProps) {
  return (
    <div class="chat-view-tabs" data-view={props.value} role="tablist" aria-label="Chat view">
      <Button
        variant="bare"
        role="tab"
        id="chat-view-tab"
        aria-selected={props.value === "chat"}
        aria-controls="chat-view-panel"
        onClick={() => props.onChange("chat")}
      >
        Chat
      </Button>
      <Button
        variant="bare"
        role="tab"
        id="events-view-tab"
        aria-selected={props.value === "events"}
        aria-controls="events-view-panel"
        onClick={() => props.onChange("events")}
      >
        Events
      </Button>
    </div>
  );
}
