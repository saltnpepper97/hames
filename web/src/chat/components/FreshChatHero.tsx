import { Show, createMemo, onMount } from "solid-js";
import { AgentAvatar } from "../../agents/AgentAvatar";
import { useAgentDirectory } from "../../agents/AgentDirectory";
import { fallbackAvatar } from "../../agents/color";
import { Icon } from "../../shell/icons";

interface FreshChatHeroProps {
  agentId?: string;
  pending?: boolean;
}

export function FreshChatHero(props: FreshChatHeroProps) {
  const directory = useAgentDirectory();
  const agent = createMemo(() => directory.agents().find((candidate) => candidate.id === props.agentId));

  onMount(() => {
    if (props.agentId) void directory.ensureLoaded();
  });

  return (
    <div class="fresh-chat-hero" aria-busy={props.pending || undefined}>
      <Show
        when={agent()}
        fallback={<span class="fresh-chat-mark"><Icon name="brand.mark" size={42} /></span>}
      >
        {(selected) => (
          <AgentAvatar
            config={selected().avatar ?? fallbackAvatar(selected().id)}
            name={selected().name}
            size={48}
          />
        )}
      </Show>
      <h2>What should we work on?</h2>
      <p>
        <Show when={agent()} fallback="Hames is ready when you are.">
          {(selected) => `${selected().name} is ready when you are.`}
        </Show>
      </p>
    </div>
  );
}
