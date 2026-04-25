import { useMemo, useState } from "react"
import { ArrowDownRight, ArrowUpRight, ChevronRight, SlidersHorizontal, Zap } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { useSignals } from "@/hooks/use-data"
import { cn } from "@/lib/utils"

export default function Signals() {
  const { data, isLoading, error } = useSignals(100)
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState<"all" | "active" | "pending">("all")
  const [direction, setDirection] = useState<"all" | "BUY" | "SELL">("all")
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    return (data?.signals ?? []).filter((signal) => {
      if (status !== "all" && signal.status !== status) return false
      if (direction !== "all" && signal.direction !== direction) return false
      if (
        q &&
        !`${signal.type} ${signal.strategy_type ?? ""} ${signal.price ?? ""} ${signal.basis} ${signal.timeframe} ${signal.direction}`
          .toLowerCase()
          .includes(q)
      ) {
        return false
      }
      return true
    })
  }, [data?.signals, direction, search, status])

  const selected = filtered.find((item) => item.id === selectedId) ?? filtered[0] ?? null

  return (
    <div className="p-4 md:p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-foreground">Signals</h1>
          <p className="text-sm text-muted-foreground">{filtered.length} live signal{filtered.length === 1 ? "" : "s"}</p>
        </div>
        <Badge variant="gold" className="text-[10px]">Supabase-backed</Badge>
      </div>

      <Card>
        <CardContent className="pt-4 space-y-3">
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1">
              <SlidersHorizontal className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                className="pl-9"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search price, timeframe, confirmation..."
              />
            </div>
            <Pill options={["all", "active", "pending"]} active={status} onChange={(v) => setStatus(v as typeof status)} />
            <Pill options={["all", "BUY", "SELL"]} active={direction} onChange={(v) => setDirection(v as typeof direction)} />
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_360px]">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle>Signal List</CardTitle>
            <CardDescription>Live forward-test signals flowing in from the API by strategy.</CardDescription>
          </CardHeader>
          <CardContent className="pt-0 space-y-3">
            {isLoading ? <State label="Loading signals..." /> : null}
            {error instanceof Error ? <State label={error.message} tone="error" /> : null}
            {!isLoading && !error && !filtered.length ? <State label="No signals available for the current filters." /> : null}
            {!isLoading && !error ? filtered.map((signal) => (
              <button
                key={signal.id}
                type="button"
                onClick={() => setSelectedId(signal.id)}
                className={cn(
                  "w-full rounded-xl border p-4 text-left transition-all",
                  selected?.id === signal.id ? "border-gold-500/35 bg-gold-500/5" : "border-ap-border bg-ap-surface/35 hover:bg-ap-surface/55"
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={signal.type === "Gap" ? "gold" : "outline"} className="text-[10px]">{signal.type}</Badge>
                      <Badge variant="purple" className="text-[10px]">{formatStrategy(signal.strategy_type)}</Badge>
                      <Badge variant={signal.direction === "BUY" ? "buy" : "sell"} className="text-[10px]">
                        {signal.direction}
                      </Badge>
                      <Badge variant={signal.status === "active" ? "buy" : "muted"} className="text-[10px]">
                        {signal.status}
                      </Badge>
                    </div>
                    <div className="num text-base font-bold text-foreground">{signal.price?.toFixed(2) ?? "--"}</div>
                    <div className="text-[11px] text-muted-foreground">{signal.basis} | {signal.timeframe} | {signal.session_name ?? "--"}</div>
                    <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
                      <span>Quality <span className="num text-gold-400">Q{signal.quality}</span></span>
                      <span>Bias {signal.h4_bias ?? "--"} / {signal.bias_strength ?? "--"}</span>
                    </div>
                  </div>
                  <ChevronRight className="h-4 w-4 text-muted-foreground/40" />
                </div>
              </button>
            )) : null}
          </CardContent>
        </Card>

        <Card className="sticky top-5">
          <CardHeader className="pb-3">
            <CardTitle>Signal Detail</CardTitle>
            <CardDescription>Inspect the selected live signal without changing the page design language.</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">
            {!selected ? (
              <State label="Select a signal to inspect its detail." />
            ) : (
              <div className="space-y-4">
                <div className="rounded-xl border border-ap-border bg-ap-surface/35 p-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="num text-lg font-bold text-foreground">{selected.price?.toFixed(2) ?? "--"}</div>
                      <div className="text-[11px] text-muted-foreground">{selected.timeframe} | {selected.basis}</div>
                    </div>
                    <div className={cn("inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-semibold", selected.direction === "BUY" ? "border-buy/25 bg-buy/10 text-buy" : "border-sell/25 bg-sell/10 text-sell")}>
                      {selected.direction === "BUY" ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
                      {selected.direction}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <Meta label="Type" value={selected.type} />
                  <Meta label="Strategy" value={formatStrategy(selected.strategy_type)} />
                  <Meta label="Status" value={selected.status} />
                  <Meta label="Quality" value={`Q${selected.quality}`} />
                  <Meta label="Bias" value={selected.h4_bias ?? "--"} />
                  <Meta label="Bias Strength" value={selected.bias_strength ?? "--"} />
                  <Meta label="Displacement" value={selected.displacement.toFixed(1)} />
                  <Meta label="Session" value={selected.session_name ?? "--"} />
                  <Meta label="Created" value={new Date(selected.created_at).toLocaleString()} />
                </div>

                <div className="rounded-xl border border-ap-border bg-ap-surface/35 p-4">
                  <div className="flex items-center gap-2">
                    <Zap className="h-4 w-4 text-gold-400" />
                    <span className="label-xs">Execution Context</span>
                  </div>
                  <div className="mt-3 space-y-2 text-xs text-muted-foreground">
                    <p>Level price: <span className="num text-foreground">{selected.level_price?.toFixed(2) ?? "--"}</span></p>
                    <p>Touch count: <span className="num text-foreground">{selected.touch_count}</span></p>
                    <p>Break count: <span className="num text-foreground">{selected.break_count}</span></p>
                    <p>Confirmation score: <span className="num text-foreground">{selected.confirmation_score ?? "--"}</span></p>
                    <p>Quality rejections: <span className="num text-foreground">{selected.quality_rejection_count ?? "--"}</span></p>
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function formatStrategy(value?: string | null) {
  return (value ?? "gap_sweep").replace(/_/g, " ")
}

function Pill({ options, active, onChange }: { options: string[]; active: string; onChange: (value: string) => void }) {
  return (
    <div className="flex items-center gap-1 bg-ap-surface rounded-lg border border-ap-border p-0.5">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          className={cn(
            "rounded-md px-2.5 py-1 text-[11px] font-medium capitalize transition-colors",
            active === option ? "bg-ap-card text-foreground border border-ap-border" : "text-muted-foreground hover:text-foreground"
          )}
        >
          {option}
        </button>
      ))}
    </div>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-2">
      <div className="label-xs">{label}</div>
      <div className="mt-1 text-xs font-semibold text-foreground">{value}</div>
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
