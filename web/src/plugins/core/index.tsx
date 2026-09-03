import { useParams } from "@solidjs/router";
import { createMemo } from "solid-js";
import { ChatPage } from "../../pages/ChatPage";
import { PlaceholderPage } from "../../pages/PlaceholderPage";
import type { WebPlugin } from "../../shell/plugins";
import { useWorkspace } from "../../shell/workspace";
import { coreConversationNodes } from "./conversationNodes";
import { coreComposerControls } from "./composerControls";

function ChatSurface() {
  const params = useParams<{ sessionId?: string }>();
  const workspace = useWorkspace();
  const selectedSession = createMemo(() =>
    params.sessionId ? workspace.session(params.sessionId) : undefined,
  );

  return (
    <ChatPage
      connection={workspace.connection()}
      error={workspace.error()}
      selectedSession={selectedSession()}
      onRetry={() => void workspace.refresh()}
      onSessionChanged={() => void workspace.refresh()}
      onSessionUpdated={workspace.updateSession}
    />
  );
}

const placeholder = (
  eyebrow: string,
  title: string,
  description: string,
  detail: string,
) => () => (
  <PlaceholderPage
    eyebrow={eyebrow}
    title={title}
    description={description}
    detail={detail}
  />
);

export const coreWebPlugin = {
  id: "hames.core",
  conversationNodes: coreConversationNodes,
  composerControls: coreComposerControls,
  surfaces: [
    {
      id: "chat",
      path: "/chat",
      route: ["/chat", "/chat/:sessionId"],
      label: "Chat",
      icon: "nav.chat",
      component: ChatSurface,
      sidebar: { kind: "conversations" },
    },
    {
      id: "runs",
      path: "/runs",
      route: "/runs",
      label: "Runs",
      icon: "nav.runs",
      component: placeholder(
        "Provenance",
        "Runs",
        "Trace model calls, context, tools, policy, and child-agent work.",
        "Run timelines and exact inspection links will arrive with the chat vertical slice.",
      ),
      sidebar: { kind: "section", description: "Timelines and provenance" },
    },
    {
      id: "agents",
      path: "/agents",
      route: "/agents",
      label: "Agents",
      icon: "nav.agents",
      component: placeholder(
        "Identity",
        "Agents",
        "Manage portable agent roles and their effective authority.",
        "Agent editing will continue to write validated AGENT.md capsules through the gateway.",
      ),
      sidebar: { kind: "section", description: "Roles and authority" },
    },
    {
      id: "memory",
      path: "/memory",
      route: "/memory",
      label: "Memory",
      icon: "nav.memory",
      component: placeholder(
        "Continuity",
        "Memory",
        "Inspect semantic, relationship, operational, and episodic memory.",
        "Memory records will remain provenance-linked and scoped by the existing runtime.",
      ),
      sidebar: { kind: "section", description: "Continuity and recall" },
    },
    {
      id: "skills",
      path: "/skills",
      route: "/skills",
      label: "Skills",
      icon: "nav.skills",
      component: placeholder(
        "Procedures",
        "Skills",
        "Review active procedures, evidence, validation, and version history.",
        "Promotion and rollback will call gateway controls rather than writing files in-browser.",
      ),
      sidebar: { kind: "section", description: "Procedures and evidence" },
    },
    {
      id: "scars",
      path: "/scars",
      route: "/scars",
      label: "Scars",
      icon: "nav.scars",
      component: placeholder(
        "Evolution",
        "Scars",
        "Understand corrections, repair candidates, guards, and regressions.",
        "Evidence and repair lineage will be reconstructed from durable gateway events.",
      ),
      sidebar: { kind: "section", description: "Corrections and repair" },
    },
    {
      id: "plugins",
      path: "/plugins",
      route: "/plugins",
      label: "Plugins",
      icon: "nav.plugins",
      component: placeholder(
        "Extensions",
        "Plugins",
        "Inspect capabilities, permissions, isolation, and runtime health.",
        "Unsafe execution and permission changes will remain explicit and visually prominent.",
      ),
      sidebar: { kind: "section", description: "Extensions and access" },
    },
    {
      id: "settings",
      path: "/settings",
      route: "/settings",
      label: "Settings",
      icon: "nav.settings",
      component: placeholder(
        "Local configuration",
        "Settings",
        "Control safe runtime defaults without exposing stored secrets.",
        "Provider credentials will never be returned to or retained by the browser.",
      ),
      sidebar: { kind: "section", description: "Local configuration" },
    },
  ],
} satisfies WebPlugin;
