import {
  executeUserCommand, cancelGoal, compactSession, createGoal, dreamSession, forkSession,
  getCurrentGoal, healScars, pauseGoal, resumeGoal, stopBackgroundTerminals,
} from "../api/client";
import type { Session } from "../api/types";
import type { WebCommand } from "./webCommands";

export function commandWorkTitle(command: WebCommand): string | undefined {
  switch (command.kind) {
    case "custom": return `Run /${command.name}`;
    case "dream": return "Dream";
    case "heal": return "Heal scars";
    case "compact": return "Compact conversation";
    case "goal": return command.action === "start" ? command.objective
      : command.action === "resume" ? "Resume goal" : undefined;
    default: return undefined;
  }
}

export interface CommandOutcome {
  note: string;
  openedSession?: Session;
}

export async function executeWebCommand(command: WebCommand, sessionId: string): Promise<CommandOutcome> {
  switch (command.kind) {
    case "custom":
      return { note: `/${command.name} started`,
        openedSession: await executeUserCommand(sessionId, command.name, command.note) };
    case "dream":
      await dreamSession(sessionId);
      return { note: "Dream started" };
    case "compact":
      await compactSession(sessionId);
      return { note: "Compaction started" };
    case "heal": {
      const accepted = await healScars(sessionId);
      return { note: accepted.disposition === "queued" ? "Scar repair queued" : "Scar repair started" };
    }
    case "fork":
      return { note: "Conversation forked", openedSession: await forkSession(sessionId, command.at) };
    case "stop": {
      const stopped = await stopBackgroundTerminals(sessionId);
      return { note: stopped.closed === 0 ? "No background terminals are running"
        : `Closed ${stopped.closed} background ${stopped.closed === 1 ? "terminal" : "terminals"}` };
    }
    case "goal": {
      const goal = command.action === "show" ? await getCurrentGoal(sessionId)
        : command.action === "start" ? await createGoal(sessionId, command.objective ?? "")
        : command.action === "pause" ? await pauseGoal(sessionId)
        : command.action === "resume" ? await resumeGoal(sessionId)
        : await cancelGoal(sessionId);
      return { note: goal ? `${goal.status[0]!.toUpperCase()}${goal.status.slice(1)} goal · ${goal.objective}`
        : "No active goal" };
    }
  }
}
