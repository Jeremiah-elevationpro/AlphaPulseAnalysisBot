import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { fmt } from "@/lib/utils"
import type { ReplayResultResponse } from "@/lib/api"

export function ReplayResults({ result }: { result?: ReplayResultResponse | null }) {
  if (!result) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Replay Results</CardTitle>
        </CardHeader>
        <CardContent className="pt-0 text-sm text-muted-foreground">No replay result available yet.</CardContent>
      </Card>
    )
  }

  return (
    <Card glow>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>Replay Results</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">Spencer completed replay analysis for {result.symbol}.</p>
          </div>
          <Badge variant="gold" className="text-[10px]">Run #{result.runId}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="Symbol" value={result.symbol} />
          <Stat label="Period" value={result.period} />
          <Stat label="Activated Trades" value={String(result.activatedTrades)} />
          <Stat label="Wins / Losses" value={`${result.wins} / ${result.losses}`} />
          <Stat label="Win Rate" value={`${fmt(result.winRate, 1)}%`} />
          <Stat label="TP1 / TP2 / TP3" value={`${fmt(result.tp1Rate, 1)}% / ${fmt(result.tp2Rate, 1)}% / ${fmt(result.tp3Rate, 1)}%`} />
          <Stat label="Net Pips" value={`${result.netPips >= 0 ? "+" : ""}${fmt(result.netPips, 1)}`} />
          <Stat label="Avg Pips / Trade" value={fmt(result.averagePipsPerTrade, 1)} />
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          <BreakdownCard title="Micro Confirmation Breakdown" data={result.microConfirmationBreakdown} />
          <BreakdownCard title="Session Breakdown" data={result.sessionBreakdown} />
          <BreakdownCard title="Bias Breakdown" data={result.biasBreakdown} />
        </div>

        <div className="overflow-hidden rounded-xl border border-ap-border">
          <table className="w-full text-left text-sm">
            <thead className="bg-ap-surface/75 text-xs uppercase tracking-[0.14em] text-muted-foreground">
              <tr>
                <th className="px-4 py-3">Pair</th>
                <th className="px-4 py-3">Direction</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Entry</th>
              </tr>
            </thead>
            <tbody>
              {(result.sampleTrades ?? []).slice(0, 6).map((trade, index) => (
                <tr key={index} className="border-t border-ap-border bg-ap-card/70">
                  <td className="px-4 py-3 font-medium text-foreground">{String(trade.pair ?? trade.symbol ?? "--")}</td>
                  <td className="px-4 py-3 text-muted-foreground">{String(trade.direction ?? "--")}</td>
                  <td className="px-4 py-3 text-muted-foreground">{String(trade.status ?? "--")}</td>
                  <td className="px-4 py-3 font-mono text-foreground">{formatMaybeNumber(trade.entry_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-ap-border bg-ap-surface/45 px-4 py-3">
      <div className="label-xs">{label}</div>
      <div className="mt-2 text-sm font-semibold text-foreground">{value}</div>
    </div>
  )
}

function BreakdownCard({ title, data }: { title: string; data: Record<string, unknown> }) {
  const entries = Object.entries(data ?? {})
  return (
    <div className="rounded-xl border border-ap-border bg-ap-surface/45 p-4">
      <div className="text-sm font-semibold text-foreground">{title}</div>
      <div className="mt-3 space-y-2">
        {entries.length ? (
          entries.map(([key, value]) => (
            <div key={key} className="rounded-lg border border-ap-border bg-ap-card/40 px-3 py-2">
              <div className="text-sm capitalize text-muted-foreground">{key.replace(/_/g, " ")}</div>
              <div className="mt-1 text-sm font-mono text-foreground">{formatBreakdownValue(value)}</div>
            </div>
          ))
        ) : (
          <div className="text-sm text-muted-foreground">No data available.</div>
        )}
      </div>
    </div>
  )
}

function formatMaybeNumber(value: unknown) {
  return typeof value === "number" ? value.toFixed(2) : "--"
}

function formatBreakdownValue(value: unknown) {
  if (typeof value === "number") return String(value)
  if (typeof value === "string") return value
  if (!value || typeof value !== "object") return "--"

  const record = value as Record<string, unknown>
  const parts: string[] = []

  if (typeof record.activated === "number") parts.push(`activated ${record.activated}`)
  if (typeof record.wins === "number" && typeof record.losses === "number") {
    parts.push(`W/L ${record.wins}/${record.losses}`)
  }
  if (typeof record.win_rate === "number") parts.push(`win ${fmt(record.win_rate, 1)}%`)
  if (typeof record.tp1_hit_rate === "number") parts.push(`TP1 ${fmt(record.tp1_hit_rate, 1)}%`)
  if (typeof record.net_pips === "number") parts.push(`net ${fmt(record.net_pips, 1)}p`)
  if (typeof record.avg_pips_per_trade === "number") parts.push(`avg ${fmt(record.avg_pips_per_trade, 1)}p`)

  if (parts.length) return parts.join(" · ")
  return JSON.stringify(value)
}
