export const settingsCategories = [
  { id: "connections", label: "Connections", description: "Providers and account access" },
  {
    id: "appearance",
    label: "Appearance",
    description: "Theme for this browser",
  },
  {
    id: "usage",
    label: "Usage",
    description: "Tokens, limits, and activity",
  },
] as const;

export type SettingsCategoryId = (typeof settingsCategories)[number]["id"];

export function settingsCategory(id: string) {
  return settingsCategories.find((category) => category.id === id);
}
