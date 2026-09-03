import {
  IconBandage,
  IconBrain,
  IconGitBranch,
  IconHorse,
  IconMessageCircle,
  IconMessages,
  IconPlug,
  IconRobotFace,
  IconSettings,
  IconTool,
} from "@tabler/icons-solidjs";
import type { IconPackPlugin } from "../../shell/icons";

export const tablerIconPack = {
  id: "tabler",
  label: "Tabler Icons",
  icons: {
    "brand.mark": IconHorse,
    "nav.chat": IconMessageCircle,
    "nav.runs": IconGitBranch,
    "nav.agents": IconRobotFace,
    "nav.memory": IconBrain,
    "nav.skills": IconTool,
    "nav.scars": IconBandage,
    "nav.plugins": IconPlug,
    "nav.settings": IconSettings,
    "state.empty": IconMessages,
  },
} satisfies IconPackPlugin;
