import type { Component } from "solid-js";
import type { Session } from "../api/types";
import type { ConversationNode } from "../chat/projection";
import type { SemanticIconName } from "./icons";

export type ContextSidebarContribution =
  | { readonly kind: "conversations" }
  | {
      readonly kind: "component";
      readonly component: Component;
      readonly action?: Component;
      readonly searchable?: boolean;
    }
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
  readonly composerControls?: readonly ComposerControlContribution[];
  readonly composerActions?: readonly ComposerActionContribution[];
}

export interface ConversationNodeContribution {
  readonly kind: ConversationNode["kind"];
  readonly component: Component<{ node: ConversationNode }>;
}

export type ComposerControlSeat = "left" | "right";

export interface ComposerControlProps {
  session: Session;
  disabled: boolean;
  onSessionUpdated: (session: Session) => void;
  onError: (message: string) => void;
  onOpenActions: () => void;
}

export interface ComposerControlContribution {
  readonly id: string;
  readonly seat: ComposerControlSeat;
  readonly order: number;
  readonly component: Component<ComposerControlProps>;
}

export interface ComposerActionProps {
  disabled: boolean;
  onUpload: () => void;
  onCommands: () => void;
}

export interface ComposerActionContribution {
  readonly id: string;
  readonly order: number;
  readonly component: Component<ComposerActionProps>;
}

export interface WebPluginRegistry {
  readonly plugins: readonly WebPlugin[];
  readonly surfaces: readonly WebSurfaceContribution[];
  readonly conversationNodes: ReadonlyMap<ConversationNode["kind"], ConversationNodeContribution>;
  readonly composerControls: ReadonlyMap<ComposerControlSeat, readonly ComposerControlContribution[]>;
  readonly composerActions: readonly ComposerActionContribution[];
}

export function composeWebPlugins(plugins: readonly WebPlugin[]): WebPluginRegistry {
  const surfaces = plugins.flatMap((plugin) => plugin.surfaces);
  const surfaceIds = new Set<string>();
  const surfacePaths = new Set<string>();
  const conversationNodes = new Map<ConversationNode["kind"], ConversationNodeContribution>();
  const composerControlIds = new Set<string>();
  const composerControls = new Map<ComposerControlSeat, readonly ComposerControlContribution[]>();
  const composerActionIds = new Set<string>();
  const composerActions = plugins
    .flatMap((plugin) => plugin.composerActions ?? [])
    .sort((left, right) => left.order - right.order);

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

  for (const seat of ["left", "right"] as const) {
    const contributions = plugins
      .flatMap((plugin) => plugin.composerControls ?? [])
      .filter((contribution) => contribution.seat === seat)
      .sort((left, right) => left.order - right.order);
    for (const contribution of contributions) {
      if (composerControlIds.has(contribution.id)) {
        throw new Error(`Duplicate composer control id: ${contribution.id}`);
      }
      composerControlIds.add(contribution.id);
    }
    composerControls.set(seat, contributions);
  }

  for (const contribution of composerActions) {
    if (composerActionIds.has(contribution.id)) {
      throw new Error(`Duplicate composer action id: ${contribution.id}`);
    }
    composerActionIds.add(contribution.id);
  }

  return { plugins, surfaces, conversationNodes, composerControls, composerActions };
}
