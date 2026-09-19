import { Show, createEffect, createMemo } from "solid-js";
import { useAgentDirectory } from "../../agents/AgentDirectory";
import { Spinner } from "../../components/Spinner";

interface FreshChatHeroProps {
  agentId?: string;
  pending?: boolean;
}

export function FreshChatHero(props: FreshChatHeroProps) {
  const directory = useAgentDirectory();
  const agentName = createMemo(() => {
    const agentId = props.agentId;
    if (!agentId) return "";
    return directory.agents().find((candidate) => candidate.id === agentId)?.name ?? "";
  });

  createEffect(() => {
    void directory.ensureLoaded();
  });

  return (
    <div class="fresh-chat-hero" aria-busy={props.pending || undefined}>
      <Show when={props.pending}>
        <span class="fresh-chat-mark">
          <Spinner size="xl" label="Starting chat" />
        </span>
      </Show>
      <h2>{props.pending ? "Starting a new chat" : "What should we work on?"}</h2>
      <p>
        <Show when={!props.pending} fallback="Preparing the workspace…">
          {agentName() ? `${agentName()} is ready when you are.` : "Hames is ready when you are."}
        </Show>
      </p>
    </div>
  );
}
