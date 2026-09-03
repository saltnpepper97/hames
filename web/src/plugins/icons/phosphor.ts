import agents from "@phosphor-icons/core/regular/robot.svg";
import chat from "@phosphor-icons/core/regular/chat-circle-dots.svg";
import empty from "@phosphor-icons/core/regular/chats-circle.svg";
import memory from "@phosphor-icons/core/regular/brain.svg";
import plugins from "@phosphor-icons/core/regular/plugs-connected.svg";
import runs from "@phosphor-icons/core/regular/git-branch.svg";
import scars from "@phosphor-icons/core/regular/warning-diamond.svg";
import settings from "@phosphor-icons/core/regular/gear-six.svg";
import skills from "@phosphor-icons/core/regular/wrench.svg";
import type { IconPackPlugin } from "../../shell/icons";

export const phosphorIconPack = {
  id: "phosphor",
  label: "Phosphor Regular",
  icons: {
    "nav.chat": chat,
    "nav.runs": runs,
    "nav.agents": agents,
    "nav.memory": memory,
    "nav.skills": skills,
    "nav.scars": scars,
    "nav.plugins": plugins,
    "nav.settings": settings,
    "state.empty": empty,
  },
} satisfies IconPackPlugin;
