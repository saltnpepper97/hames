export interface WebBootstrap {
  protocol_version: number;
  gateway_protocol_version: number;
  working_directory: string;
  csrf_token: string;
}

export interface GatewayHealth {
  status: string;
  version: string;
  protocol_version: number;
  database_ready: boolean;
  provider_profiles: string[];
  default_provider: string;
  active_runs: number;
  active_terminals: number;
  mcp_servers: number;
  mcp_ready: number;
  mcp_degraded: number;
}

export interface Session {
  id: string;
  created_at: string;
  status: string;
  title: string | null;
  working_directory: string;
  agent_id: string;
  provider: string;
  model: string;
  reasoning_effort: string;
  interaction_mode: string;
}

export interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  };
}

export interface DashboardSnapshot {
  bootstrap: WebBootstrap;
  health: GatewayHealth;
  sessions: Session[];
}
