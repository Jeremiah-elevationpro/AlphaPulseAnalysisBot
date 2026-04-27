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
  useHealth,
  useMarketContext,
  useSignals,
  useTrades,
} from "@/hooks/use-data"
import { cn } from "@/lib/utils"

export default function Dashboard() {
  const health = useHealth()
  const analytics = useAnalytics()
  const signals = useSignals(6)
  const trades = useActiveTrades(6)
  const allTrades = useTrades("all", 200)
  const alerts = useAlerts(6)
  const botStatus = useBotStatus()
  const isBotOnline = ["online", "analyzing", "watching", "starting"].includes(botStatus.data?.status ?? "")
  const market = useMarketContext(botStatus.data?.symbol ?? "XAUUSD", isBotOnline)

  const metrics = analytics.data?.metrics
  const marketContext = market.data
  const dominantBias = marketContext?.bias?.dominant ?? "neutral"
  const botWindowActive = Boolean(marketContext?.session?.botWindowActive)
  const strategyStats = useMemo(() => buildStrategyStats(allTrades.data?.trades ?? []), [allTrades.data?.trades])

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
          accent={health.data?.status === "ok" ? "buy" : "sell"}
        >
          <div className="flex items-center justify-between">
            <div className="inline-flex items-center gap-2 text-xs font-semibold">
              <StatusDot status={health.data?.status === "ok" ? "online" : "offline"} pulse />
              {health.isLoading ? "Loading..." : health.data?.status === "ok" ? "Online & Running" : "Degraded"}
            </div>
            <span className="text-[10px] text-muted-foreground">
              {health.data?.timestamp ? new Date(health.data.timestamp).toLocaleTimeString() : "--"}
            </span>
          </div>
          <InfoRow label="Database" value={health.data?.db_connected ? "Connected" : "Disconnected"} />
          <InfoRow label="Active Trades" value={String(health.data?.active_trades ?? 0)} mono />
          <InfoRow label="Uptime" value={health.data ? `${Math.floor(health.data.uptime / 60)}m` : "--"} mono />
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
        </StatusCard>

        <StatusCard
          icon={BarChart2}
          title="Replay Edge"
          subtitle={analytics.isLoading ? "Loading performance" : "Latest replay-backed metrics"}
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

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <StrategySummaryCard title="Gap Sweep" stats={strategyStats.gap_sweep} />
        <StrategySummaryCard title="Engulfing Rejection" stats={strategyStats.engulfing_rejection} />
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
}: {
  title: string
  stats: { live: number; closed: number; wins: number; losses: number; netPips: number; winRate: string }
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-2 pt-0 md:grid-cols-4">
        <MetaBox label="Live Setups" value={String(stats.live)} tone="gold" />
        <MetaBox label="Wins / Losses" value={`${stats.wins}/${stats.losses}`} tone={stats.wins >= stats.losses ? "buy" : "sell"} />
        <MetaBox label="Net Pips" value={`${stats.netPips >= 0 ? "+" : ""}${stats.netPips.toFixed(1)}`} tone={stats.netPips >= 0 ? "buy" : "sell"} />
        <MetaBox label="Win Rate" value={stats.winRate} tone="gold" />
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
  return (value ?? "gap_sweep").replace(/_/g, " ")
}

function buildStrategyStats(trades: Array<{ strategy_type?: string | null; status: string; result: string | null; realized_pips?: number | null }>) {
  const seed = {
    gap_sweep: { live: 0, closed: 0, wins: 0, losses: 0, netPips: 0 },
    engulfing_rejection: { live: 0, closed: 0, wins: 0, losses: 0, netPips: 0 },
  }
  for (const trade of trades) {
    const key = trade.strategy_type === "engulfing_rejection" ? "engulfing_rejection" : "gap_sweep"
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
    gap_sweep: { ...seed.gap_sweep, winRate: seed.gap_sweep.closed ? `${Math.round((seed.gap_sweep.wins / seed.gap_sweep.closed) * 100)}%` : "0%" },
    engulfing_rejection: {
      ...seed.engulfing_rejection,
      winRate: seed.engulfing_rejection.closed ? `${Math.round((seed.engulfing_rejection.wins / seed.engulfing_rejection.closed) * 100)}%` : "0%",
    },
  }
}

function biasColor(value?: string | null) {
  if (value === "bullish") return "text-buy"
  if (value === "bearish") return "text-sell"
  return "text-gold-300"
}
