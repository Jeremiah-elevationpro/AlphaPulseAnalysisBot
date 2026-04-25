import { useMemo, useState } from "react"
import { ChevronRight, Target } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import type { TradeRow } from "@/lib/api"
import { useTrades } from "@/hooks/use-data"
import { cn } from "@/lib/utils"

export default function Trades() {
  const activeQuery = useTrades("active", 100)
  const closedQuery = useTrades("closed", 100)
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null)

  const activeTrades = activeQuery.data?.trades ?? []
  const closedTrades = closedQuery.data?.trades ?? []
  const selectedTrade =
    [...activeTrades, ...closedTrades].find((trade) => trade.uuid === selectedUuid) ??
    activeTrades[0] ??
    closedTrades[0] ??
    null

  const stats = useMemo(() => {
    const closed = closedTrades
    const wins = closed.filter((trade) => String(trade.result ?? trade.status).toLowerCase().includes("win") || trade.status === "COMPLETED").length
    const netPips = closed.reduce((sum, trade) => sum + (trade.realized_pips ?? 0), 0)
    return {
      active: activeTrades.length,
      history: closed.length,
      winRate: closed.length ? `${Math.round((wins / closed.length) * 100)}%` : "0%",
      netPips: `${netPips >= 0 ? "+" : ""}${netPips.toFixed(1)}`,
    }
  }, [activeTrades.length, closedTrades])

  return (
    <div className="p-4 md:p-6 space-y-5">
      <div className="fixed inset-x-0 top-0 h-44 bg-glow-gold-top pointer-events-none" />

      <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div className="space-y-1">
          <div className="inline-flex items-center gap-2 rounded-full border border-gold-500/20 bg-gold-500/8 px-3 py-1 text-[11px] font-semibold text-gold-400">
            <Target className="h-3.5 w-3.5" />
            Trade Tracking Console
          </div>
          <h1 className="text-xl font-bold text-foreground md:text-2xl">Trades</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">Track active and historical trades from the real bot data flow.</p>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Metric label="Active" value={String(stats.active)} accent="gold" />
          <Metric label="History" value={String(stats.history)} accent="muted" />
          <Metric label="Win Rate" value={stats.winRate} accent="buy" />
          <Metric label="Net Pips" value={stats.netPips} accent={stats.netPips.startsWith("-") ? "sell" : "buy"} />
        </div>
      </div>

      <Section
        title="Active Trades"
        subtitle="Open positions and managed runners."
        loading={activeQuery.isLoading}
        error={activeQuery.error instanceof Error ? activeQuery.error.message : null}
        empty={!activeTrades.length}
      >
        <TradeTable trades={activeTrades} selectedUuid={selectedTrade?.uuid ?? null} onSelect={setSelectedUuid} />
      </Section>

      <Section
        title="Trade History"
        subtitle="Closed trades, results, and realized pip outcomes."
        loading={closedQuery.isLoading}
        error={closedQuery.error instanceof Error ? closedQuery.error.message : null}
        empty={!closedTrades.length}
      >
        <TradeTable trades={closedTrades} selectedUuid={selectedTrade?.uuid ?? null} onSelect={setSelectedUuid} history />
      </Section>

      <Card className="sticky top-5">
        <CardHeader className="pb-3">
          <CardTitle>Trade Detail</CardTitle>
          <CardDescription>Timeline-style summary using the fields already stored by the bot.</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          {!selectedTrade ? (
            <State label="Select a trade to inspect its details." />
          ) : (
            <div className="space-y-4">
              <div className="rounded-xl border border-ap-border bg-ap-surface/35 p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="num text-lg font-bold text-foreground">{selectedTrade.pair}</span>
                      <Badge variant="purple" className="text-[10px]">{formatStrategy(selectedTrade.strategy_type)}</Badge>
                      <Badge variant={selectedTrade.direction === "BUY" ? "buy" : "sell"} className="text-[10px]">
                        {selectedTrade.direction}
                      </Badge>
                    </div>
                    <div className="mt-1 text-[11px] text-muted-foreground">
                      {selectedTrade.higher_tf ?? "--"} {"->"} {selectedTrade.lower_tf ?? "--"} | {selectedTrade.confirmation_type ?? "manual"} | {selectedTrade.session_name ?? "--"}
                    </div>
                  </div>
                  <Badge variant="gold" className="text-[10px]">{selectedTrade.status}</Badge>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                <Meta label="Entry" value={selectedTrade.entry_price?.toFixed(2) ?? "--"} mono />
                <Meta label="SL" value={selectedTrade.sl_price?.toFixed(2) ?? "--"} mono tone="sell" />
                <Meta label="TP1" value={selectedTrade.tp1?.toFixed(2) ?? "--"} mono tone="buy" />
                <Meta label="TP2 / TP3" value={`${selectedTrade.tp2?.toFixed(2) ?? "--"} / ${selectedTrade.tp3?.toFixed(2) ?? "--"}`} mono />
              </div>

              <div className="rounded-xl border border-ap-border bg-ap-surface/35 p-4">
                <div className="flex items-center justify-between">
                  <span className="label-xs">TP Progress</span>
                  <span className="num text-xs font-semibold text-gold-400">{selectedTrade.tp_progress_reached ?? 0}/3</span>
                </div>
                <div className="mt-2 h-2 rounded-full bg-ap-border overflow-hidden">
                  <div className="h-full rounded-full bg-gold-500" style={{ width: `${Math.min(((selectedTrade.tp_progress_reached ?? 0) / 3) * 100, 100)}%` }} />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <Meta label="Activated" value={selectedTrade.activated_at ? new Date(selectedTrade.activated_at).toLocaleString() : "--"} />
                <Meta label="Closed" value={selectedTrade.closed_at ? new Date(selectedTrade.closed_at).toLocaleString() : "Still active"} />
                <Meta label="Realized / Unrealized" value={`${selectedTrade.realized_pips?.toFixed(1) ?? "--"}p`} mono tone={(selectedTrade.realized_pips ?? 0) >= 0 ? "buy" : "sell"} />
                <Meta label="Final Result" value={selectedTrade.result ?? selectedTrade.status} />
                <Meta label="Bias" value={`${selectedTrade.dominant_bias ?? selectedTrade.h4_bias ?? "--"} / ${selectedTrade.bias_strength ?? "--"}`} />
                <Meta label="Conf Score" value={selectedTrade.confirmation_score != null ? `${selectedTrade.confirmation_score}` : "--"} />
                <Meta label="Quality Rejects" value={selectedTrade.quality_rejection_count != null ? `${selectedTrade.quality_rejection_count}` : "--"} />
                <Meta label="Structure Breaks" value={selectedTrade.structure_break_count != null ? `${selectedTrade.structure_break_count}` : "--"} />
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function Section({
  title,
  subtitle,
  loading,
  error,
  empty,
  children,
}: {
  title: string
  subtitle: string
  loading: boolean
  error: string | null
  empty: boolean
  children: React.ReactNode
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle>{title}</CardTitle>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>
      <CardContent className="pt-0">
        {loading ? <State label="Loading trades..." /> : null}
        {!loading && error ? <State label={error} tone="error" /> : null}
        {!loading && !error && empty ? <State label="No trades available in this section." /> : null}
        {!loading && !error && !empty ? children : null}
      </CardContent>
    </Card>
  )
}

function TradeTable({
  trades,
  selectedUuid,
  onSelect,
  history = false,
}: {
  trades: TradeRow[]
  selectedUuid: string | null
  onSelect: (uuid: string | null) => void
  history?: boolean
}) {
  return (
    <div className="space-y-3">
      {trades.map((trade) => (
        <button
          key={trade.uuid ?? trade.id}
          type="button"
          onClick={() => onSelect(trade.uuid ?? null)}
          className={cn(
            "w-full rounded-xl border p-4 text-left transition-all",
            selectedUuid === trade.uuid ? "border-gold-500/35 bg-gold-500/5" : "border-ap-border bg-ap-surface/35 hover:bg-ap-surface/55"
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="num text-sm font-bold text-foreground">{trade.pair}</span>
                <Badge variant="purple" className="text-[10px]">{formatStrategy(trade.strategy_type)}</Badge>
                <Badge variant={trade.direction === "BUY" ? "buy" : "sell"} className="text-[10px]">
                  {trade.direction}
                </Badge>
                <Badge variant="gold" className="text-[10px]">{trade.status}</Badge>
              </div>
              <div className="grid grid-cols-2 gap-2 text-[11px] text-muted-foreground sm:grid-cols-4">
                <span>Entry <span className="num text-foreground">{trade.entry_price?.toFixed(2) ?? "--"}</span></span>
                <span>TP1 <span className="num text-buy">{trade.tp1?.toFixed(2) ?? "--"}</span></span>
                <span>{history ? "Result" : "Progress"} <span className="text-foreground">{history ? trade.result ?? trade.status : `${trade.tp_progress_reached ?? 0}/3`}</span></span>
                <span>Pips <span className={cn("num", (trade.realized_pips ?? 0) >= 0 ? "text-buy" : "text-sell")}>{trade.realized_pips?.toFixed(1) ?? "--"}</span></span>
              </div>
            </div>
            <ChevronRight className="h-4 w-4 text-muted-foreground/40" />
          </div>
        </button>
      ))}
    </div>
  )
}

function formatStrategy(value?: string | null) {
  return (value ?? "gap_sweep").replace(/_/g, " ")
}

function Metric({ label, value, accent }: { label: string; value: string; accent: "gold" | "buy" | "sell" | "muted" }) {
  const color = accent === "buy" ? "text-buy" : accent === "sell" ? "text-sell" : accent === "gold" ? "text-gold-400" : "text-foreground"
  return (
    <Card>
      <CardContent className="py-4">
        <div className="label-xs">{label}</div>
        <div className={cn("num mt-2 text-lg font-bold", color)}>{value}</div>
      </CardContent>
    </Card>
  )
}

function Meta({ label, value, mono, tone }: { label: string; value: string; mono?: boolean; tone?: "buy" | "sell" }) {
  return (
    <div className="rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-2">
      <div className="label-xs">{label}</div>
      <div className={cn("mt-1 text-xs font-semibold text-foreground", mono && "num", tone === "buy" && "text-buy", tone === "sell" && "text-sell")}>{value}</div>
    </div>
  )
}

function State({ label, tone = "muted" }: { label: string; tone?: "muted" | "error" }) {
  return (
    <div className="rounded-xl border border-ap-border bg-ap-surface/35 px-4 py-10 text-center">
      <p className={cn("text-sm", tone === "error" ? "text-sell" : "text-muted-foreground")}>{label}</p>
    </div>
  )
}
