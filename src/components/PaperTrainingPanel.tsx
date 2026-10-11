export type TrainingBook = {
  id: string; starting_balance: number; cash: number; equity: number; net_pnl_usd: number;
  return_pct: number; completed_trades: number; open_positions: number; pending_orders: number;
  unique_completed_episodes: number; wins: number; losses: number; max_drawdown_pct: number;
  failed_executions: number; feasibility: number | null; rejected_signals?: number;
  rejection_reasons: Record<string, number>; risk_halt: string | null;
  positions: { symbol: string; address: string; opened_at: number; valuation: string; committed_usd: number }[];
  recent_trades: { symbol: string; closed_at: number; pnl_usd: number; exit_reason: string }[];
};
type Comparison = { net_improvement_usd: number; paired_episode_count: number;
  approximate_cluster_mean_95_ci: number[] | null; stress_net_pnl_usd: number };
export type PaperTrainingSnapshot = {
  status: string; paper_only: boolean; active_version?: string; simulation_count?: number;
  unique_market_episodes?: number; unique_observations?: number; duplicate_observations?: number;
  books?: TrainingBook[]; control_comparison?: Comparison; monitor_comparison?: Comparison | null;
  last_training?: { at: number; status: string; candidate?: string; rejected_checks?: string[] } | null;
  versions?: { id: string; status: string; at: number }[];
  recorder?: { backlog: number; dropped: number; processed: number; error: string | null };
  error?: string; reason?: string;
};

export default function PaperTrainingPanel(_: { data?: PaperTrainingSnapshot }) {
  return null;
}
