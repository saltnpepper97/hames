import type { Component } from "solid-js";
import type { ConversationNode } from "../chat/projection";
import type { SemanticIconName } from "./icons";

export type ContextSidebarContribution =
  | { readonly kind: "conversations" }
  | { readonly kind: "section"; readonly description: string };

export interface WebSurfaceContribution {
  readonly id: string;
  readonly path: string;
  readonly route: string | string[];
  readonly label: string;
  readonly icon: SemanticIconName;
  readonly component: Component;
  readonly sidebar: ContextSidebarContribution;
}

export interface WebPlugin {
  readonly id: string;
  readonly surfaces: readonly WebSurfaceContribution[];
  readonly conversationNodes?: readonly ConversationNodeContribution[];
}

export interface ConversationNodeContribution {
  readonly kind: ConversationNode["kind"];
  readonly component: Component<{ node: ConversationNode }>;
}

export interface WebPluginRegistry {
  readonly plugins: readonly WebPlugin[];
  readonly surfaces: readonly WebSurfaceContribution[];
  readonly conversationNodes: ReadonlyMap<ConversationNode["kind"], ConversationNodeContribution>;
}

export function composeWebPlugins(plugins: readonly WebPlugin[]): WebPluginRegistry {
  const surfaces = plugins.flatMap((plugin) => plugin.surfaces);
  const surfaceIds = new Set<string>();
  const surfacePaths = new Set<string>();
  const conversationNodes = new Map<ConversationNode["kind"], ConversationNodeContribution>();

  for (const surface of surfaces) {
    if (surfaceIds.has(surface.id)) {
      throw new Error(`Duplicate web surface id: ${surface.id}`);
    }
    if (surfacePaths.has(surface.path)) {
      throw new Error(`Duplicate web surface path: ${surface.path}`);
    }
    surfaceIds.add(surface.id);
    surfacePaths.add(surface.path);
  }

  for (const contribution of plugins.flatMap((plugin) => plugin.conversationNodes ?? [])) {
    if (conversationNodes.has(contribution.kind)) {
      throw new Error(`Duplicate conversation node renderer: ${contribution.kind}`);
    }
    conversationNodes.set(contribution.kind, contribution);
  }

  return { plugins, surfaces, conversationNodes };
}
