import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart2,
  Bell,
  Globe2,
  Layers,
} from "lucide-react"
import { useMemo } from "react"
import { Link } from "react-router-dom"

import { BotControlPanel } from "@/components/control/BotControlPanel"
import { ReplayRunner } from "@/components/control/ReplayRunner"
import { SpencerStatus } from "@/components/control/SpencerStatus"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { StatusDot } from "@/components/ui/status-dot"
import {
  useActiveTrades,
  useAlerts,
  useAnalytics,
  useBotStatus,
  useDataHealth,
  useHealth,
  useLearningProfiles,
  useMarketContext,
  useSignals,
  useTrades,
} from "@/hooks/use-data"
import type { LearningProfileSummary } from "@/lib/api"
import { cn } from "@/lib/utils"

export default function Dashboard() {
  const health = useHealth()
  const dataHealth = useDataHealth()
  const analytics = useAnalytics()
  const signals = useSignals(6)
  const trades = useActiveTrades(6)
  const allTrades = useTrades("all", 200)
  const alerts = useAlerts(6)
  const botStatus = useBotStatus()
  const learningProfiles = useLearningProfiles()
  const isBotOnline = ["online", "analyzing", "watching", "starting"].includes(botStatus.data?.status ?? "")
  const market = useMarketContext(botStatus.data?.symbol ?? "XAUUSD", isBotOnline)

  const metrics = analytics.data?.metrics
  const marketContext = market.data
  const dominantBias = marketContext?.bias?.dominant ?? "neutral"
  const strategyStats = useMemo(() => buildStrategyStats(allTrades.data?.trades ?? []), [allTrades.data?.trades])
  const learningMap = useMemo(
    () => Object.fromEntries((learningProfiles.data?.profiles ?? []).map((profile) => [profile.strategy_type, profile])),
    [learningProfiles.data?.profiles]
  )
  const marketPlan = botStatus.data?.data?.marketPlan
  const fiveLayerStatus = botStatus.data?.data?.fiveLayerStatus

  return (
    <div className="space-y-5 p-4 md:p-6">
      <div className="pointer-events-none fixed inset-x-0 top-0 h-44 bg-glow-purple-top" />
      <div className="pointer-events-none fixed inset-x-0 top-0 h-28 bg-glow-gold-top" />

      <Card glow>
        <CardContent className="relative overflow-hidden py-5">
          <div className="pointer-events-none absolute inset-y-0 right-0 w-1/3 bg-gradient-to-l from-purple-500/14 via-gold-500/8 to-transparent" />
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="space-y-2">
              <div className="inline-flex items-center gap-2 rounded-full border border-gold-500/20 bg-gold-500/8 px-3 py-1 text-[11px] font-semibold text-gold-300">
                <Activity className="h-3.5 w-3.5" />
                FX Unfiltered Dashboard
              </div>
              <div>
                <h2 className="text-xl font-bold text-foreground md:text-2xl">Powered by AlphaPulse</h2>
                <p className="max-w-2xl text-sm text-muted-foreground">
                  Spencer is monitoring the live engine, replay flow, signals, trades, and alerts from one premium control surface.
                </p>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Badge variant="gold" className="text-[10px]">Engine: AlphaPulse</Badge>
              <Badge variant="purple" className="text-[10px]">Assistant: Spencer</Badge>
              <Badge variant={health.data?.db_connected ? "buy" : "sell"} className="text-[10px]">
                {health.data?.db_connected ? "Supabase Connected" : "DB Unavailable"}
              </Badge>
              <Button asChild size="sm">
                <Link to="/signals">Review Signals</Link>
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link to="/settings">Open Controls</Link>
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <SpencerStatus status={botStatus.data} />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <StatusCard
          icon={Activity}
          title="AlphaPulse Engine Status"
          subtitle={health.isLoading ? "Checking runtime" : `v${health.data?.version ?? "1.0.0"}`}
          accent={isEngineHealthy(health.data?.status) ? "buy" : "sell"}
        >
          <div className="flex items-center justify-between">
            <div className="inline-flex items-center gap-2 text-xs font-semibold">
              <StatusDot status={isEngineHealthy(health.data?.status) ? "online" : "offline"} pulse />
              {health.isLoading ? "Loading..." : isEngineHealthy(health.data?.status) ? "Online & Running" : "Degraded"}
            </div>
            <span className="text-[10px] text-muted-foreground">
              {health.data?.timestamp ? new Date(health.data.timestamp).toLocaleTimeString() : "--"}
            </span>
          </div>
          <InfoRow label="Database" value={health.data?.db_connected ? "Connected" : "Disconnected"} />
          <InfoRow label="Active Trades" value={String(health.data?.active_trades ?? 0)} mono />
          <InfoRow label="Uptime" value={formatUptime(health.data)} mono />
          <InfoRow label="Data Health" value={dataHealth.data?.warnings?.length ? "Warnings" : "Healthy"} />
          <div className="pt-1">
            <Badge variant="outline" className="text-[10px]">Source: Live Bot</Badge>
          </div>
        </StatusCard>

        <StatusCard
          icon={Globe2}
          title="Market Context"
          subtitle={
            market.isLoading
              ? "Loading live context"
              : `${formatSessionName(marketContext?.session?.sessionName)} · 24/7 Active`
          }
          accent="gold"
        >
          <InfoRow label="Symbol" value={marketContext?.symbol ?? botStatus.data?.symbol ?? "XAUUSD"} mono />
          <InfoRow label="Current Price" value={formatMaybeNumber(marketContext?.currentPrice)} mono />
          <InfoRow label="Bid" value={formatMaybeNumber(marketContext?.bid)} mono />
          <InfoRow label="Ask" value={formatMaybeNumber(marketContext?.ask)} mono />
          <InfoRow label="Spread" value={marketContext?.spreadPips != null ? `${marketContext.spreadPips.toFixed(1)} pips` : "Unavailable"} mono />
          <InfoRow label="D1 Bias" value={formatBiasLabel(marketContext?.bias?.d1)} valueClass={biasColor(marketContext?.bias?.d1)} />
          <InfoRow label="H4 Bias" value={formatBiasLabel(marketContext?.bias?.h4)} valueClass={biasColor(marketContext?.bias?.h4)} />
          <InfoRow label="H1 Bias" value={formatBiasLabel(marketContext?.bias?.h1)} valueClass={biasColor(marketContext?.bias?.h1)} />
          <InfoRow label="Dominant Bias" value={formatBiasLabel(dominantBias)} valueClass={biasColor(dominantBias)} />
          <InfoRow label="Bias Strength" value={marketContext?.bias?.strength ?? "Unavailable"} />
          <InfoRow
            label="Operating Mode"
            value="24/7 Active"
            valueClass="text-buy"
          />
          <InfoRow
            label="Market Session"
            value={formatSessionName(marketContext?.session?.sessionName)}
          />
          <InfoRow label="Local Time" value={marketContext?.session?.localTime ?? "—"} mono />
          <InfoRow label="Last Updated" value={marketContext?.timestamp ? new Date(marketContext.timestamp).toLocaleTimeString() : "Unavailable"} mono />
          <div className="pt-1 flex flex-wrap gap-2">
            <Badge variant="outline" className="text-[10px]">Source: Live Bot</Badge>
            {marketContext?.source ? <Badge variant="gold" className="text-[10px]">{marketContext.source}</Badge> : null}
          </div>
        </StatusCard>

        <StatusCard
          icon={BarChart2}
          title="Learning / Replay Performance"
          subtitle={analytics.isLoading ? "Loading performance" : "Secondary to live analyst context"}
          accent="gold"
        >
          <InfoRow label="Win Rate" value={`${metrics?.win_rate ?? 0}%`} mono valueClass="text-buy" />
          <InfoRow label="TP1 Hit Rate" value={`${metrics?.tp1_hit_rate ?? 0}%`} mono />
          <InfoRow
            label="Net Pips"
            value={`${(metrics?.net_pips ?? 0) > 0 ? "+" : ""}${metrics?.net_pips ?? 0}`}
            mono
            valueClass={(metrics?.net_pips ?? 0) >= 0 ? "text-buy" : "text-sell"}
          />
          <InfoRow label="Avg Pips / Trade" value={`${metrics?.avg_pips_per_trade ?? 0}`} mono />
          <div className="pt-1 flex flex-wrap gap-2">
            {(analytics.data?.sources ?? []).map((source) => (
              <Badge key={source.key} variant={source.tone === "buy" ? "buy" : source.tone === "gold" ? "gold" : source.tone === "warn" ? "sell" : source.tone === "purple" ? "purple" : "outline"} className="text-[10px]">
                Source: {source.label}
              </Badge>
            ))}
          </div>
        </StatusCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <BotControlPanel />
        <ReplayRunner />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-4">
        <MetricCard label="Signals" value={String(signals.data?.total ?? 0)} accent="gold" />
        <MetricCard label="Active Trades" value={String(trades.data?.total ?? 0)} accent="buy" />
        <MetricCard label="Alerts" value={String(alerts.data?.total ?? 0)} accent={alerts.data?.total ? "warn" : "muted"} />
        <MetricCard label="Replay Trades" value={String(metrics?.total_trades ?? 0)} accent="gold" />
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle>Strategy Live Status</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          {[
            "gap_liquidity_sweep_reclaim",
            "engulfing_rejection",
            "standard_break_retest",
            "failed_engulf_break_retest",
          ].map((strategy) => {
            const scan = botStatus.data?.data?.strategyScans?.[strategy]
            const enabled = scan?.enabled ?? botStatus.data?.data?.liveEnabledStrategies?.includes(strategy) ?? false
            const researchOnly = botStatus.data?.data?.researchOnlyStrategies?.includes(strategy) || scan?.mode === "research_only"
            return (
              <div key={strategy} className="rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-3">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-semibold text-foreground">{formatStrategy(strategy)}</div>
                  <Badge variant={researchOnly ? "outline" : enabled ? "buy" : "gold"} className="text-[10px]">
                    {researchOnly ? "Research Only" : enabled ? "Classifier / Live Enabled" : "Disabled"}
                  </Badge>
                </div>
                <div className="mt-3 space-y-1.5 text-[11px] text-muted-foreground">
                  <div>Scans run: {scan?.scans_run ?? 0}</div>
                  <div>Candidates: {scan?.candidates_found ?? 0}</div>
                  <div>Watchlists: {scan?.watchlist_alerts_sent ?? 0}</div>
                  <div>Entries: {scan?.entry_alerts_sent ?? scan?.alerts_sent ?? 0}</div>
                  <div>Alerts failed: {scan?.alerts_failed ?? 0}</div>
                  <div>Duplicates: {scan?.duplicates_blocked ?? 0}</div>
                  <div>Last result: {scan?.last_result ?? "--"}</div>
                  <div>Reject: {scan?.last_reject_reason || "--"}</div>
                </div>
              </div>
            )
          })}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle>Spencer Market Analyst</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
            <div className="text-xs font-semibold text-gold-300">Current Bias</div>
            <div className={cn("text-sm font-semibold", biasColor(marketPlan?.dominant_bias))}>
              {formatBiasLabel(marketPlan?.dominant_bias)} {marketPlan?.bias_strength ? `(${marketPlan.bias_strength})` : ""}
            </div>
            <Separator />
            <InfoRow label="H4 Context" value={marketPlan?.h4_context ?? "Waiting for market plan"} />
            <InfoRow label="H1 Context" value={marketPlan?.h1_context ?? "—"} />
            <InfoRow label="M15 Context" value={marketPlan?.m15_context ?? "—"} />
            <InfoRow
              label="Waiting For"
              value={(marketPlan?.confirmation_waiting_for ?? []).slice(0, 5).join(" / ") || "sweep reclaim / failed retest / break-retest close / displacement"}
            />
          </div>
          <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
            <div className="text-xs font-semibold text-gold-300">Scenario Map</div>
            <InfoRow
              label="Primary"
              value={marketPlan?.primary_scenario ? `${marketPlan.primary_scenario.direction} ${marketPlan.primary_scenario.watch_zone}` : "Waiting for primary scenario"}
            />
            <InfoRow
              label="Secondary"
              value={marketPlan?.secondary_scenario ? `${marketPlan.secondary_scenario.direction} ${marketPlan.secondary_scenario.watch_zone}` : "Waiting for secondary scenario"}
            />
            <InfoRow
              label="Actionable Psych"
              value={(marketPlan?.actionable_psych_levels ?? marketPlan?.psychological_levels ?? [])
                .slice(0, 12)
                .map((v) => v.toFixed(2))
                .join(" | ") || "—"}
            />
            <InfoRow
              label="Key Levels"
              value={[
                ...(marketPlan?.key_supports ?? []).slice(0, 2).map((v) => `S ${v.toFixed(2)}`),
                ...(marketPlan?.key_resistances ?? []).slice(0, 2).map((v) => `R ${v.toFixed(2)}`),
              ].join(" | ") || "—"}
            />
            <InfoRow
              label="Active Watch Zones"
              value={(marketPlan?.active_watch_zones ?? [])
                .slice(0, 3)
                .map((zone) => `${zone.direction} ${zone.level_low.toFixed(2)}-${zone.level_high.toFixed(2)} (${zone.status})`)
                .join(" | ") || "No active watch zones"}
            />
            <InfoRow label="Last Scenario Update" value={marketPlan?.last_updated ? new Date(marketPlan.last_updated).toLocaleString() : "—"} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle>Five-Layer Intelligence</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 xl:grid-cols-5">
          <LayerCard title="Market Analyst" value={String((fiveLayerStatus?.market_analyst as Record<string, unknown> | undefined)?.plan_status ?? "waiting")} detail={String((fiveLayerStatus?.market_analyst as Record<string, unknown> | undefined)?.primary_scenario ?? "No scenario yet")} />
          <LayerCard title="Confirmation Engine" value={String((fiveLayerStatus?.confirmation_engine as Record<string, unknown> | undefined)?.status ?? "waiting")} detail={String((fiveLayerStatus?.confirmation_engine as Record<string, unknown> | undefined)?.reason ?? "Waiting for confirmation")} />
          <LayerCard title="Learning Score" value={String((fiveLayerStatus?.learning_score as Record<string, unknown> | undefined)?.recommended_action ?? "allow")} detail={`Score ${(fiveLayerStatus?.learning_score as Record<string, unknown> | undefined)?.final_score ?? "--"}`} />
          <LayerCard title="Decision Engine" value={String((fiveLayerStatus?.decision_engine as Record<string, unknown> | undefined)?.action ?? "wait")} detail={String((fiveLayerStatus?.decision_engine as Record<string, unknown> | undefined)?.reason ?? "Waiting")} />
          <LayerCard title="Risk Management" value={String((fiveLayerStatus?.risk_management as Record<string, unknown> | undefined)?.current_status ?? "idle")} detail={String((fiveLayerStatus?.risk_management as Record<string, unknown> | undefined)?.final_result ?? "No active setup")} />
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <StrategySummaryCard title="Gap Sweep" stats={strategyStats.gap_liquidity_sweep_reclaim} learning={learningMap.gap_liquidity_sweep_reclaim} liveEnabled />
        <StrategySummaryCard title="Engulfing Rejection" stats={strategyStats.engulfing_rejection} learning={learningMap.engulfing_rejection} liveEnabled />
        <StrategySummaryCard title="Break + Retest" stats={strategyStats.standard_break_retest} learning={learningMap.standard_break_retest} liveEnabled />
        <StrategySummaryCard title="Failed Engulf Break + Retest" stats={strategyStats.failed_engulf_break_retest} learning={learningMap.failed_engulf_break_retest} researchOnly lowSampleThreshold={20} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <FeedCard
          title="Recent Signals"
          actionLabel="View all"
          actionTo="/signals"
          loading={signals.isLoading}
          error={signals.error instanceof Error ? signals.error.message : null}
          empty={!signals.data?.signals.length}
          emptyLabel="No signals available yet."
        >
          {signals.data?.signals.map((signal) => (
            <div key={signal.id} className="flex items-center justify-between rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-2.5">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                      <Badge variant={signal.type === "Gap" ? "gold" : "outline"} className="text-[10px]">{signal.type}</Badge>
                      <Badge variant="purple" className="text-[10px]">{formatStrategy(signal.strategy_type)}</Badge>
                      <span className={cn("flex items-center gap-1 text-xs font-semibold", signal.direction === "BUY" ? "text-buy" : "text-sell")}>
                    {signal.direction === "BUY" ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
                    {signal.direction}
                  </span>
                </div>
                <div className="num text-xs text-foreground">{signal.price?.toFixed(2) ?? "--"}</div>
                <div className="text-[10px] text-muted-foreground">{signal.basis} | {signal.timeframe} | {signal.session_name ?? "session n/a"}</div>
              </div>
              <div className="text-right">
                <div className="num text-sm font-bold text-gold-300">Q{signal.quality}</div>
                <div className="text-[10px] text-muted-foreground">{signal.status}</div>
              </div>
            </div>
          ))}
        </FeedCard>

        <FeedCard
          title="Active Trades"
          actionLabel="Open trades"
          actionTo="/trades"
          loading={trades.isLoading}
          error={trades.error instanceof Error ? trades.error.message : null}
          empty={!trades.data?.trades.length}
          emptyLabel="No active trades right now."
        >
          {trades.data?.trades.map((trade) => (
            <div key={trade.id} className="rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-2.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="num text-xs font-bold text-foreground">{trade.pair}</span>
                  <span className={cn("flex items-center gap-1 text-xs font-semibold", trade.direction === "BUY" ? "text-buy" : "text-sell")}>
                    {trade.direction === "BUY" ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
                    {trade.direction}
                  </span>
                  <Badge variant="purple" className="text-[10px]">{formatStrategy(trade.strategy_type)}</Badge>
                </div>
                <Badge variant="gold" className="text-[10px]">{trade.status}</Badge>
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] text-muted-foreground">
                <span>Entry <span className="num text-foreground">{trade.entry_price?.toFixed(2) ?? "--"}</span></span>
                <span>TP1 <span className="num text-buy">{trade.tp1?.toFixed(2) ?? "--"}</span></span>
                <span>Pips <span className={cn("num", (trade.realized_pips ?? 0) >= 0 ? "text-buy" : "text-sell")}>{trade.realized_pips?.toFixed(1) ?? "--"}</span></span>
              </div>
            </div>
          ))}
        </FeedCard>

        <FeedCard
          title="Recent Alerts"
          actionLabel="Open alerts"
          actionTo="/alerts"
          loading={alerts.isLoading}
          error={alerts.error instanceof Error ? alerts.error.message : null}
          empty={!alerts.data?.alerts.length}
          emptyLabel="No alerts in the feed."
        >
          {alerts.data?.alerts.map((alert) => (
            <div key={alert.id} className="rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-2.5">
              <div className="flex items-center gap-2">
                <Bell className={cn("h-3.5 w-3.5", alert.priority === "critical" ? "text-sell" : alert.priority === "high" ? "text-warn" : "text-gold-300")} />
                <span className="text-xs font-semibold text-foreground">{alert.title}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">{alert.message}</p>
              <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
                <span>{alert.related_label}</span>
                <span>{new Date(alert.timestamp).toLocaleString()}</span>
              </div>
            </div>
          ))}
        </FeedCard>
      </div>
    </div>
  )
}

function StatusCard({
  icon: Icon,
  title,
  subtitle,
  accent,
  children,
}: {
  icon: typeof Activity
  title: string
  subtitle: string
  accent: "buy" | "sell" | "gold"
  children: React.ReactNode
}) {
  const accentClass =
    accent === "buy"
      ? "border-buy/20 bg-buy/10 text-buy"
      : accent === "sell"
      ? "border-sell/20 bg-sell/10 text-sell"
      : "border-gold-500/20 bg-gold-500/10 text-gold-300"
  return (
    <Card className="h-full">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-3">
          <div className={cn("flex h-9 w-9 items-center justify-center rounded-lg border", accentClass)}>
            <Icon className="h-4 w-4" />
          </div>
          <div>
            <CardTitle className="text-sm">{title}</CardTitle>
            <p className="text-[10px] text-muted-foreground">{subtitle}</p>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-2 pt-0">{children}</CardContent>
    </Card>
  )
}

function MetricCard({ label, value, accent }: { label: string; value: string; accent: "gold" | "buy" | "warn" | "muted" }) {
  const color = accent === "buy" ? "text-buy" : accent === "warn" ? "text-warn" : accent === "gold" ? "text-gold-300" : "text-foreground"
  return (
    <Card>
      <CardContent className="py-4">
        <div className="label-xs">{label}</div>
        <div className={cn("num mt-2 text-xl font-bold", color)}>{value}</div>
      </CardContent>
    </Card>
  )
}

function StrategySummaryCard({
  title,
  stats,
  learning,
  liveEnabled = false,
  researchOnly = false,
  lowSampleThreshold = 0,
}: {
  title: string
  stats: { live: number; closed: number; wins: number; losses: number; netPips: number; winRate: string }
  learning?: LearningProfileSummary
  liveEnabled?: boolean
  researchOnly?: boolean
  lowSampleThreshold?: number
}) {
  const sampleSize = learning?.sample_size ?? 0
  const isLowSample = lowSampleThreshold > 0 && sampleSize < lowSampleThreshold

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <MetaBox label="Live Setups" value={String(stats.live)} tone="gold" />
          <MetaBox label="Wins / Losses" value={`${stats.wins}/${stats.losses}`} tone={stats.wins >= stats.losses ? "buy" : "sell"} />
          <MetaBox label="Net Pips" value={`${stats.netPips >= 0 ? "+" : ""}${stats.netPips.toFixed(1)}`} tone={stats.netPips >= 0 ? "buy" : "sell"} />
          <MetaBox label="Win Rate" value={stats.winRate} tone="gold" />
        </div>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <MetaBox label="Learning Sample" value={String(sampleSize)} tone="gold" />
          <MetaBox label="Learning Win Rate" value={learning ? `${learning.win_rate.toFixed(1)}%` : "No data"} tone={learning && learning.win_rate >= 50 ? "buy" : "gold"} />
          <MetaBox label="Best Session" value={learning?.best_session ? formatSessionName(learning.best_session) : "No data"} tone="gold" />
          <MetaBox label="Best Timeframe" value={learning?.best_timeframe ?? "No data"} tone="gold" />
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline" className="text-[10px]">Source: Strategy Learning</Badge>
          {researchOnly ? (
            <>
              <Badge variant="outline" className="text-[10px]">Research Only</Badge>
              {isLowSample && <Badge variant="gold" className="text-[10px]">Low Sample</Badge>}
            </>
          ) : liveEnabled ? (
            <Badge variant="buy" className="text-[10px]">Live: Enabled</Badge>
          ) : (
            <Badge variant="gold" className="text-[10px]">
              {learning?.status === "ready" ? "Ready" : learning?.status ?? "No data"}
            </Badge>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function FeedCard({
  title,
  actionLabel,
  actionTo,
  loading,
  error,
  empty,
  emptyLabel,
  children,
}: {
  title: string
  actionLabel: string
  actionTo: string
  loading: boolean
  error: string | null
  empty: boolean
  emptyLabel: string
  children: React.ReactNode
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>{title}</CardTitle>
          <Button asChild variant="ghost" size="sm">
            <Link to={actionTo}>{actionLabel}</Link>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        {loading ? <StateBlock icon={Activity} label="Loading live data..." /> : null}
        {!loading && error ? <StateBlock icon={AlertTriangle} label={error} tone="error" /> : null}
        {!loading && !error && empty ? <StateBlock icon={Layers} label={emptyLabel} /> : null}
        {!loading && !error && !empty ? children : null}
      </CardContent>
    </Card>
  )
}

function StateBlock({ icon: Icon, label, tone = "muted" }: { icon: typeof Activity; label: string; tone?: "muted" | "error" }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-4">
      <Icon className={cn("h-4 w-4", tone === "error" ? "text-sell" : "text-muted-foreground")} />
      <span className={cn("text-sm", tone === "error" ? "text-sell" : "text-muted-foreground")}>{label}</span>
    </div>
  )
}

function InfoRow({ label, value, mono, valueClass }: { label: string; value: string; mono?: boolean; valueClass?: string }) {
  return (
    <>
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-muted-foreground">{label}</span>
        <span className={cn("text-[10px] text-foreground", mono ? "font-mono" : "", valueClass)}>{value}</span>
      </div>
      <Separator />
    </>
  )
}

function MetaBox({ label, value, tone }: { label: string; value: string; tone: "gold" | "buy" | "sell" }) {
  const color = tone === "buy" ? "text-buy" : tone === "sell" ? "text-sell" : "text-gold-300"
  return (
    <div className="rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-3">
      <div className="label-xs">{label}</div>
      <div className={cn("num mt-2 text-sm font-bold", color)}>{value}</div>
    </div>
  )
}

function LayerCard({ title, value, detail }: { title: string; value: string; detail: string }) {
  return (
    <div className="rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-3">
      <div className="label-xs">{title}</div>
      <div className="mt-2 text-sm font-semibold text-foreground">{value}</div>
      <div className="mt-1 text-xs text-muted-foreground">{detail}</div>
    </div>
  )
}

function formatMaybeNumber(value?: number | null) {
  return value != null ? value.toFixed(2) : "Unavailable"
}

function formatBiasLabel(value?: string | null) {
  return value ? value.replace(/_/g, " ") : "Unavailable"
}

function formatSessionName(value?: string | null) {
  if (!value || value === "off_session" || value === "quiet_session") return "Quiet Session"
  if (value === "overlap") return "Overlap"
  if (value === "london") return "London"
  if (value === "new_york") return "New York"
  if (value === "asia") return "Asia"
  return value.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
}

function formatStrategy(value?: string | null) {
  const canonical = value ?? "gap_liquidity_sweep_reclaim"
  const labels: Record<string, string> = {
    gap_sweep: "Gap Sweep",
    gap_liquidity_sweep_reclaim: "Gap Sweep",
    engulfing: "Engulfing Rejection",
    engulfing_rejection: "Engulfing Rejection",
    break_retest: "Break + Retest",
    standard_break_retest: "Break + Retest",
    failed_engulf: "Failed Engulf Break + Retest",
    failed_engulf_break_retest: "Failed Engulf Break + Retest",
  }
  return labels[canonical] ?? canonical.replace(/_/g, " ")
}

function buildStrategyStats(trades: Array<{ strategy_type?: string | null; status: string; result: string | null; realized_pips?: number | null }>) {
  const seed = {
    gap_liquidity_sweep_reclaim: { live: 0, closed: 0, wins: 0, losses: 0, netPips: 0 },
    engulfing_rejection: { live: 0, closed: 0, wins: 0, losses: 0, netPips: 0 },
    standard_break_retest: { live: 0, closed: 0, wins: 0, losses: 0, netPips: 0 },
    failed_engulf_break_retest: { live: 0, closed: 0, wins: 0, losses: 0, netPips: 0 },
  }
  for (const trade of trades) {
    const key: keyof typeof seed =
      trade.strategy_type === "gap_liquidity_sweep_reclaim" ||
      trade.strategy_type === "engulfing_rejection" ||
      trade.strategy_type === "standard_break_retest" ||
      trade.strategy_type === "failed_engulf_break_retest"
        ? trade.strategy_type
        : "gap_liquidity_sweep_reclaim"
    const bucket = seed[key]
    const closed = ["COMPLETED", "STOP_LOSS_HIT", "CANCELLED"].includes(trade.status) || Boolean(trade.result)
    if (closed) {
      bucket.closed += 1
      if (String(trade.result ?? trade.status).toLowerCase().includes("win") || trade.status === "COMPLETED") bucket.wins += 1
      if (String(trade.result ?? trade.status).toLowerCase().includes("loss") || trade.status === "STOP_LOSS_HIT") bucket.losses += 1
      bucket.netPips += trade.realized_pips ?? 0
    } else {
      bucket.live += 1
    }
  }
  return {
    gap_liquidity_sweep_reclaim: {
      ...seed.gap_liquidity_sweep_reclaim,
      winRate: seed.gap_liquidity_sweep_reclaim.closed ? `${Math.round((seed.gap_liquidity_sweep_reclaim.wins / seed.gap_liquidity_sweep_reclaim.closed) * 100)}%` : "0%",
    },
    engulfing_rejection: {
      ...seed.engulfing_rejection,
      winRate: seed.engulfing_rejection.closed ? `${Math.round((seed.engulfing_rejection.wins / seed.engulfing_rejection.closed) * 100)}%` : "0%",
    },
    standard_break_retest: {
      ...seed.standard_break_retest,
      winRate: seed.standard_break_retest.closed ? `${Math.round((seed.standard_break_retest.wins / seed.standard_break_retest.closed) * 100)}%` : "0%",
    },
    failed_engulf_break_retest: {
      ...seed.failed_engulf_break_retest,
      winRate: seed.failed_engulf_break_retest.closed
        ? `${Math.round((seed.failed_engulf_break_retest.wins / seed.failed_engulf_break_retest.closed) * 100)}%`
        : "0%",
    },
  }
}

function biasColor(value?: string | null) {
  if (value === "bullish") return "text-buy"
  if (value === "bearish") return "text-sell"
  return "text-gold-300"
}

function isEngineHealthy(status?: string | null): boolean {
  return status === "ok" || status === "online"
}

function formatUptime(health?: { uptime?: string; uptime_seconds?: number } | null): string {
  if (!health) return "--"
  // Prefer numeric seconds
  if (typeof health.uptime_seconds === "number" && isFinite(health.uptime_seconds)) {
    const totalMin = Math.floor(health.uptime_seconds / 60)
    const h = Math.floor(totalMin / 60)
    const m = totalMin % 60
    return h > 0 ? `${h}h ${m}m` : `${m}m`
  }
  // Parse "HH:MM:SS" string
  if (typeof health.uptime === "string" && health.uptime.includes(":")) {
    const parts = health.uptime.split(":").map(Number)
    if (parts.length === 3 && parts.every(isFinite)) {
      const [h, m] = parts
      return h > 0 ? `${h}h ${m}m` : `${m}m`
    }
  }
  return "--"
}
