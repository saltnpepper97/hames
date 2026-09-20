export interface FlowParticipant { agent: string; instructions: string; }
export interface FlowRecipe { name: string; coordinator: string; instructions: string; participants?: FlowParticipant[] | null; }
export interface FlowItem { id: string; recipe: FlowRecipe | null; error: string; }
