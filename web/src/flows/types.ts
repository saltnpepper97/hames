export interface FlowRecipe { name: string; coordinator: string; instructions: string; }
export interface FlowItem { id: string; recipe: FlowRecipe | null; error: string; }
