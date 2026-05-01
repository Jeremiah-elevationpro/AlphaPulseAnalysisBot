const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? ""

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export interface HealthResponse {
  status: "ok" | "online" | "degraded"
  db_connected: boolean
  active_trades: number
  uptime: string
  uptime_seconds: number
  version: string
  timestamp: string
}

export interface TradeRow {
  id: number
  uuid?: string | null
  pair: string
  direction: "BUY" | "SELL" | string
  entry_price: number | null
  sl_price: number | null
  tp1: number | null
  tp2: number | null
  tp3: number | null
  level_type: string | null
  level_price: number | null
  higher_tf: string | null
  lower_tf: string | null
  status: string
  result: string | null
  confidence: number | null
  strategy_type?: string | null
  source?: string | null
  setup_type?: string | null
  is_qm?: boolean
  is_psychological?: boolean
  h4_bias?: string | null
  dominant_bias?: string | null
  bias_strength?: string | null
  session_name?: string | null
  confirmation_type?: string | null
  confirmation_score?: number | null
  confirmation_path?: string | null
  quality_rejection_count?: number
  structure_break_count?: number
  level_timeframe?: string | null
  confluence_with?: string | null
  tp_progress_reached?: number
  realized_pips?: number | null
  created_at: string
  activated_at?: string | null
  closed_at?: string | null
}

export interface TradesResponse {
  trades: TradeRow[]
  total: number
  db_ready?: boolean
}

export interface SignalRow {
  id: number
  uuid?: string | null
  type: string
  price: number | null
  level_price: number | null
  direction: string
  strategy_type?: string | null
  setup_type?: string | null
  quality: number
  displacement: number
  touch_count: number
  break_count: number
  basis: string
  timeframe: string
  status: "active" | "pending"
  is_qm: boolean
  is_psych: boolean
  h4_bias: string | null
  bias_strength?: string | null
  session_name?: string | null
  confirmation_score?: number | null
  quality_rejection_count?: number | null
  created_at: string
}

export interface SignalsResponse {
  signals: SignalRow[]
  total: number
  db_ready: boolean
  error?: string
}

export interface AlertRow {
  id: string
  type: string
  title: string
  message: string
  priority: "critical" | "high" | "medium" | "low"
  read: boolean
  date_bucket: "today" | "yesterday" | "this week"
  timestamp: string
  related_label: string
  related_type: "setup" | "trade" | "system"
  symbol: string
  source: "bot" | "manual" | "system"
}

export interface AlertsResponse {
  alerts: AlertRow[]
  total: number
  db_ready: boolean
  error?: string
}

export interface AnalyticsMetrics {
  total_trades: number
  win_rate: number
  tp1_hit_rate: number
  net_pips: number
  avg_pips_per_trade: number
}

export interface AnalyticsResponse {
  db_ready: boolean
  metrics: AnalyticsMetrics
  sources?: Array<{ key: string; label: string; tone?: string }>
  charts: {
    cumulative_pips: Array<{ label: string; pips: number }>
    session_performance: Array<{ name: string; trades: number; win_rate: number; net_pips: number; tp1_rate: number }>
    micro_confirmation_performance: Array<{ name: string; trades: number; win_rate: number; net_pips: number }>
    win_loss_distribution: Array<{ name: string; value: number; color: string }>
    performance_by_bias: Array<{ name: string; trades: number; win_rate: number; net_pips: number }>
    performance_by_period: Array<{ label: string; trades: number; win_rate: number; net_pips: number }>
  }
  breakdowns: {
    session: Array<{ session: string; trades: number; wins: number; tp1: number; net_pips: number; avg_pips: number }>
    setup_type: Array<{ setup_type: string; trades: number; win_rate: number; net_pips: number }>
    micro_confirmation: Array<{ micro: string; trades: number; win_rate: number; tp1_rate: number; net_pips: number }>
    bias_gate: Array<{ bias_gate: string; trades: number; win_rate: number; net_pips: number }>
    outcome_mix: Array<{ outcome: string; trades: number; color: string }>
  }
  error?: string
}

export interface BotStatusResponse {
  success: boolean
  status: "online" | "offline" | "starting" | "stopping" | "analyzing" | "watching" | "error" | "running_replay" | "tracking_manual_setup" | string
  message: string
  timestamp: string
  // Flat backward-compat fields
  last_started_at?: string | null
  last_stopped_at?: string | null
  last_heartbeat_at?: string | null
  last_scan_at?: string | null
  last_scan_result?: string | null
  last_error?: string | null
  error_message?: string | null
  process_id?: number | null
  strategy_mode?: string | null
  symbol?: string | null
  session?: string | null
  backend_connected?: boolean
  // Rich data block (canonical — prefer over flat fields)
  data?: {
    processId?: number | null
    startedAt?: string | null
    stoppedAt?: string | null
    lastHeartbeatAt?: string | null
    lastScanAt?: string | null
    lastScanResult?: string | null
    lastError?: string | null
    errorMessage?: string | null
    currentSymbol?: string | null
    currentSession?: string | null
    strategyMode?: string | null
    // Scan pipeline summary
    lastScanSymbol?: string | null
    lastCandidatesCount?: number | null
    lastAlertsSent?: number | null
    lastRejectReason?: string | null
    lastTelegramStatus?: string | null
    lastTelegramError?: string | null
    lastScanNumber?: number | null
    sessionBlocking?: boolean | null
    instanceId?: string | null
    botWindowActive?: boolean | null
    localTime?: string | null
    activeUntil?: string | null
    currentPrice?: number | null
    bid?: number | null
    ask?: number | null
    spread?: number | null
    spreadPips?: number | null
    d1Bias?: string | null
    h4Bias?: string | null
    h1Bias?: string | null
    dominantBias?: string | null
    biasStrength?: string | null
    lastMarketUpdateAt?: string | null
    liveEnabledStrategies?: string[] | null
    researchOnlyStrategies?: string[] | null
    strategyScans?: Record<
      string,
      {
        enabled?: boolean
        mode?: string
        scans_run?: number
        candidates_found?: number
        watchlist_alerts_sent?: number
        entry_alerts_sent?: number
        alerts_sent?: number
        alerts_failed?: number
        duplicates_blocked?: number
        last_result?: string
        last_reject_reason?: string
        last_scan_time?: string
      }
    >
    operatingMode?: string | null
    marketSession?: string | null
    scanAllowed?: boolean | null
    lastAlertsFailed?: number | null
    lastTelegramAlertType?: string | null
    lastTelegramAlertTime?: string | null
    levelsDetected?: number | null
    gapLevels?: number | null
    biasPassed?: number | null
    sweepConfirmed?: number | null
    sessionPassed?: number | null
    distancePassed?: number | null
    watchlistCandidates?: number | null
    alertsSentThisScan?: number | null
    alertsFailedThisScan?: number | null
    dedupeRejections?: number | null
    totalScans?: number | null
    totalCandidatesFound?: number | null
    totalWatchlistCandidates?: number | null
    totalAlertsSent?: number | null
    totalAlertsFailed?: number | null
    totalDuplicatesBlocked?: number | null
    totalManualAlertsSent?: number | null
    totalConfirmationAlertsSent?: number | null
    marketPlan?: {
      current_price?: number | null
      h4_context?: string
      h1_context?: string
      m15_context?: string
      dominant_bias?: string
      bias_strength?: string
      targets_source?: string
      primary_scenario?: {
        direction?: string
        reason?: string
        watch_zone?: string
        invalidation?: string
        targets?: number[]
        market_plan_targets?: number[]
        trigger_conditions?: string[]
        scenario_status?: string
        played_out_note?: string
      }
      secondary_scenario?: {
        direction?: string
        reason?: string
        watch_zone?: string
        invalidation?: string
        targets?: number[]
        market_plan_targets?: number[]
        trigger_conditions?: string[]
        scenario_status?: string
        played_out_note?: string
      }
      actionable_psych_levels?: number[]
      key_supports?: number[]
      key_resistances?: number[]
      psychological_levels?: number[]
      active_watch_zones?: Array<{
        zone_id: string
        direction: string
        level_low: number
        level_high: number
        scenario_type: string
        status: string
        distance_from_current_pips?: number
        played_out_note?: string
      }>
      confirmation_waiting_for?: string[]
      last_updated?: string
    } | null
    fiveLayerStatus?: {
      market_analyst?: Record<string, unknown>
      confirmation_engine?: Record<string, unknown>
      learning_score?: Record<string, unknown>
      decision_engine?: Record<string, unknown>
      risk_management?: Record<string, unknown>
    } | null
    activeInstanceId?: string | null
    botProcessAlive?: boolean | null
    backgroundTasksActive?: number | null
    runtimeAlertsEnabled?: boolean | null
    lastShutdownTime?: string | null
    alertDedupe?: Record<string, unknown> | null
  } | null
}

export interface DataHealthResponse {
  heartbeat_ok: boolean
  telegram_ok: boolean
  active_instance_id: string | number | null
  last_scan_age_seconds: number | null
  last_alert_sent_at: string | null
  manual_tracking_count: number
  analytics_unknown_session_count: number
  analytics_unknown_micro_count: number
  warnings: string[]
}

export interface LearningProfileSummary {
  strategy_type: string
  sample_size: number
  wins: number
  losses: number
  win_rate: number
  net_pips: number
  best_session?: string | null
  best_timeframe?: string | null
  status: string
}

export interface LearningProfilesResponse {
  profiles: LearningProfileSummary[]
  db_ready: boolean
}

export interface ReplayRunPayload {
  symbol: string
  months: number
  showTrades: number
}

export interface ReplayRunResponse {
  success: boolean
  runId: number
  status: string
  message: string
}

export interface ReplayStatusResponse {
  success: boolean
  runId: number
  status: string
  message: string
  timestamp: string
}

export interface ReplayLatestResponse {
  success?: boolean
  runId?: number
  symbol?: string
  status: string
  message: string
  timestamp?: string
  started_at?: string
  completed_at?: string
  result?: ReplayResultResponse | null
}

export interface ReplayResultResponse {
  runId: number
  symbol: string
  period: string
  activatedTrades: number
  wins: number
  losses: number
  winRate: number
  tp1Rate: number
  tp2Rate: number
  tp3Rate: number
  netPips: number
  averagePipsPerTrade: number
  status: string
  microConfirmationBreakdown: Record<string, number>
  sessionBreakdown: Record<string, number>
  biasBreakdown: Record<string, number>
  sampleTrades: Array<Record<string, unknown>>
}

export interface ActivityLogEvent {
  id: number
  line: string
}

export interface ActivityLogsResponse {
  events: ActivityLogEvent[]
  source: string
  exists: boolean
}

export type RuntimeLogsResponse = ActivityLogsResponse
export type ReplayLogsResponse = ActivityLogsResponse
export type TelegramLogsResponse = ActivityLogsResponse

export interface MarketResponse {
  source: "mt5" | "db_estimate" | "cache" | "unavailable"
  price: number | null
  bid: number | null
  ask: number | null
  spread: number | null
  change: number | null
  change_pct: number | null
  high_24h: number | null
  low_24h: number | null
  bias: string
  session: string
  timestamp: string
}

export interface MarketContextResponse {
  success: boolean
  symbol: string
  message?: string
  currentPrice: number | null
  bid: number | null
  ask: number | null
  spread: number | null
  spreadPips: number | null
  bias: {
    d1: string
    h4: string
    h1: string
    dominant: string
    strength: string
  }
  session: {
    botWindowActive: boolean
    sessionName: string
    localTime: string
    activeUntil: string
  }
  timestamp: string
  source?: string
}

export interface SetupRow {
  id: number
  source?: string | null
  strategy_type?: string | null
  setup_type?: string | null
  symbol: string
  direction: "BUY" | "SELL"
  timeframe_pair: string
  entry_price: number
  stop_loss: number
  tp1: number
  tp2: number | null
  tp3: number | null
  bias: string | null
  confirmation_type: string | null
  session: string | null
  notes: string
  activation_mode: string | null
  move_sl_to_be_after_tp1: boolean
  enable_telegram_alerts: boolean
  high_priority: boolean
  status: string
  tracking_enabled?: boolean
  tracking_status?: string | null
  confirmation_required?: boolean
  telegram_alert_sent?: boolean
  telegram_alert_sent_at?: string | null
  telegram_error?: string | null
  last_alert_type?: string | null
  last_alert_time?: string | null
  current_price?: number | null
  distance_to_entry_pips?: number | null
  closed_at?: string | null
  completed_at?: string | null
  cancelled_at?: string | null
  failed_at?: string | null
  archived_at?: string | null
  completion_note?: string | null
  created_at: string
  updated_at: string
}

export interface SetupsResponse {
  setups: SetupRow[]
  total: number
  db_ready?: boolean
}

export interface SetupPayload {
  symbol: string
  direction: "BUY" | "SELL"
  timeframe_pair: string
  entry_price: number
  stop_loss: number
  tp1: number
  tp2?: number | null
  tp3?: number | null
  bias?: string | null
  confirmation_type?: string | null
  session?: string | null
  notes?: string
  activation_mode?: string | null
  move_sl_to_be_after_tp1: boolean
  enable_telegram_alerts: boolean
  high_priority: boolean
  status?: string
}

export interface SetupStatusPayload {
  status: "completed" | "cancelled" | "failed" | "expired" | "invalidated" | "archived"
  note?: string
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  dataHealth: () => request<DataHealthResponse>("/api/system/data-health"),
  market: () => request<MarketResponse>("/api/market"),
  marketContext: (symbol = "XAUUSD") => request<MarketContextResponse>(`/api/market/context?symbol=${encodeURIComponent(symbol)}`),
  trades: (status: "all" | "active" | "closed" = "all", limit = 50) =>
    request<TradesResponse>(`/api/trades?status=${status}&limit=${limit}`),
  signals: (limit = 50) => request<SignalsResponse>(`/api/signals?limit=${limit}`),
  alerts: (limit = 50) => request<AlertsResponse>(`/api/alerts?limit=${limit}`),
  analytics: (filters?: { session?: string; confirmation_type?: string; symbol?: string; source?: string }) => {
    const params = new URLSearchParams()
    if (filters?.session) params.set("session", filters.session)
    if (filters?.confirmation_type) params.set("confirmation_type", filters.confirmation_type)
    if (filters?.symbol) params.set("symbol", filters.symbol)
    if (filters?.source) params.set("source", filters.source)
    const qs = params.toString()
    return request<AnalyticsResponse>(`/api/analytics${qs ? `?${qs}` : ""}`)
  },
  bot: {
    status: () => request<BotStatusResponse>("/api/bot/status"),
    start: () => request<BotStatusResponse>("/api/bot/start", { method: "POST" }),
    stop: () => request<BotStatusResponse>("/api/bot/stop", { method: "POST" }),
    restart: () => request<BotStatusResponse>("/api/bot/restart", { method: "POST" }),
  },
  replay: {
    run: (body: ReplayRunPayload) =>
      request<ReplayRunResponse>("/api/replay/run", { method: "POST", body: JSON.stringify(body) }),
    status: (runId: number) => request<ReplayStatusResponse>(`/api/replay/status/${runId}`),
    latest: () => request<ReplayLatestResponse>("/api/replay/latest"),
    results: (runId: number) => request<ReplayResultResponse>(`/api/replay/results/${runId}`),
  },
  logs: {
    activity: (limit = 30) => request<ActivityLogsResponse>(`/api/logs/activity?limit=${limit}`),
    runtime: (limit = 100) => request<RuntimeLogsResponse>(`/api/logs/runtime?limit=${limit}`),
    replay: (limit = 100) => request<ReplayLogsResponse>(`/api/logs/replay?limit=${limit}`),
    telegram: (limit = 100) => request<TelegramLogsResponse>(`/api/logs/telegram?limit=${limit}`),
  },
  learning: {
    profiles: () => request<LearningProfilesResponse>("/api/learning/profiles"),
  },
  setups: {
    list: () => request<SetupsResponse>("/api/manual-setups"),
    active: () => request<SetupsResponse>("/api/manual-setups/active"),
    history: () => request<SetupsResponse>("/api/manual-setups/history"),
    create: (body: SetupPayload) =>
      request<SetupRow>("/api/manual-setups", { method: "POST", body: JSON.stringify(body) }),
    update: (id: number, body: Partial<SetupPayload> & { status?: string }) =>
      request<SetupRow>(`/api/manual-setups/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    updateStatus: (id: number, body: SetupStatusPayload) =>
      request<{ success: boolean; setup_id: number; status: string; tracking_enabled: boolean; closed_at: string; setup?: SetupRow }>(
        `/api/manual-setups/${id}/status`,
        { method: "PATCH", body: JSON.stringify(body) }
      ),
    resendAlert: (id: number) =>
      request<{ success: boolean; setup: SetupRow }>(`/api/manual-setups/${id}/resend-alert`, { method: "POST" }),
    delete: (id: number) => request<{ ok: boolean }>(`/api/setups/${id}`, { method: "DELETE" }),
  },
}
