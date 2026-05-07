import { useMemo, useState } from "react"
import { AlertTriangle, Bell, ChevronRight, Info, ShieldAlert } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useAlerts } from "@/hooks/use-data"
import { cn } from "@/lib/utils"

export default function Alerts() {
  const { data, isLoading, error } = useAlerts(100)
  const [typeFilter, setTypeFilter] = useState<string>("all")
  const [dateFilter, setDateFilter] = useState<string>("all")
  const [readMap, setReadMap] = useState<Record<string, boolean>>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const filtered = useMemo(() => {
    return (data?.alerts ?? []).filter((alert) => {
      if (typeFilter !== "all" && alert.type !== typeFilter) return false
      if (dateFilter !== "all" && alert.date_bucket !== dateFilter) return false
      return true
    })
  }, [data?.alerts, dateFilter, typeFilter])

  const selected = filtered.find((alert) => alert.id === selectedId) ?? filtered[0] ?? null
  const unreadCount = filtered.filter((alert) => !(readMap[alert.id] ?? alert.read)).length

  function markRead(id: string) {
    setReadMap((current) => ({ ...current, [id]: true }))
    setSelectedId(id)
  }

  return (
    <div className="p-4 md:p-6 space-y-5">
      <div className="fixed inset-x-0 top-0 h-44 bg-glow-gold-top pointer-events-none" />

      <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div className="space-y-1">
          <div className="inline-flex items-center gap-2 rounded-full border border-gold-500/20 bg-gold-500/8 px-3 py-1 text-[11px] font-semibold text-gold-400">
            <Bell className="h-3.5 w-3.5" />
            Alert Center
          </div>
          <h1 className="text-xl font-bold text-foreground md:text-2xl">Alerts</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">Centralized bot, setup, and trade notifications backed by the API alert feed.</p>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <Metric label="Visible Alerts" value={String(filtered.length)} accent="gold" />
          <Metric label="Unread" value={String(unreadCount)} accent={unreadCount ? "sell" : "muted"} />
          <Metric label="High Priority" value={String(filtered.filter((item) => item.priority === "critical" || item.priority === "high").length)} accent="warn" />
        </div>
      </div>

      <Card>
        <CardContent className="flex flex-col gap-3 py-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            {["all", ...Array.from(new Set((data?.alerts ?? []).map((item) => item.type)))].map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setTypeFilter(option)}
                className={cn(
                  "rounded-full border px-3 py-1.5 text-[11px] font-semibold transition-colors",
                  typeFilter === option ? "border-gold-500/30 bg-gold-500/12 text-gold-400" : "border-ap-border bg-ap-surface text-muted-foreground hover:text-foreground"
                )}
              >
                {option}
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <select value={dateFilter} onChange={(e) => setDateFilter(e.target.value)} className="rounded-full border border-ap-border bg-ap-surface px-3 py-1.5 text-[11px] text-foreground outline-none">
              <option value="all">all dates</option>
              <option value="today">today</option>
              <option value="yesterday">yesterday</option>
              <option value="this week">this week</option>
            </select>
            <Button variant="outline" size="sm" onClick={() => setReadMap(Object.fromEntries(filtered.map((item) => [item.id, true])))}>
              Mark all read
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_380px]">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle>Alert Feed</CardTitle>
            <CardDescription>Premium alert center with severity, read state, and linked context.</CardDescription>
          </CardHeader>
          <CardContent className="pt-0 space-y-3">
            {isLoading ? <State label="Loading alerts..." /> : null}
            {error instanceof Error ? <State label={error.message} tone="error" /> : null}
            {!isLoading && !error && !filtered.length ? <State label="No alerts match the current filters." /> : null}
            {!isLoading && !error ? filtered.map((alert) => (
              <button
                key={alert.id}
                type="button"
                onClick={() => markRead(alert.id)}
                className={cn(
                  "w-full rounded-xl border p-4 text-left transition-all",
                  selected?.id === alert.id ? "border-gold-500/35 bg-gold-500/5" : "border-ap-border bg-ap-surface/35 hover:bg-ap-surface/55",
                  alert.priority === "critical" && "border-sell/25",
                  !(readMap[alert.id] ?? alert.read) && "shadow-gold-xs"
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={variantForPriority(alert.priority)} className="text-[10px]">{alert.priority}</Badge>
                      <Badge variant="outline" className="text-[10px]">{alert.type}</Badge>
                      {!(readMap[alert.id] ?? alert.read) ? <span className="h-2 w-2 rounded-full bg-sell" /> : null}
                    </div>
                    <p className="text-sm font-semibold text-foreground">{alert.title}</p>
                    <p className="text-xs leading-6 text-muted-foreground">{alert.message}</p>
                    <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
                      <span>{alert.related_label}</span>
                      <span>{new Date(alert.timestamp).toLocaleString()}</span>
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
            <CardTitle>Alert Detail</CardTitle>
            <CardDescription>Link back to the related setup, trade, or system event.</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">
            {!selected ? (
              <State label="Select an alert to inspect its detail." />
            ) : (
              <div className="space-y-4">
                <div className="rounded-xl border border-ap-border bg-ap-surface/35 p-4">
                  <div className="flex items-start gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-ap-border bg-ap-card">
                      {iconForPriority(selected.priority)}
                    </div>
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={variantForPriority(selected.priority)} className="text-[10px]">{selected.priority}</Badge>
                        <Badge variant="outline" className="text-[10px]">{selected.source}</Badge>
                      </div>
                      <h3 className="text-sm font-semibold text-foreground">{selected.title}</h3>
                      <p className="text-xs leading-6 text-muted-foreground">{selected.message}</p>
                    </div>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Meta label="Type" value={selected.type} />
                  <Meta label="Symbol" value={selected.symbol} />
                  <Meta label="Related" value={selected.related_label} />
                  <Meta label="Date Bucket" value={selected.date_bucket} />
                  <Meta label="Timestamp" value={new Date(selected.timestamp).toLocaleString()} />
                  <Meta label="Read State" value={(readMap[selected.id] ?? selected.read) ? "read" : "unread"} />
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function variantForPriority(priority: string): "sell" | "warn" | "gold" | "muted" {
  if (priority === "critical") return "sell"
  if (priority === "high") return "warn"
  if (priority === "medium") return "gold"
  return "muted"
}

function iconForPriority(priority: string) {
  if (priority === "critical") return <ShieldAlert className="h-4 w-4 text-sell" />
  if (priority === "high") return <AlertTriangle className="h-4 w-4 text-warn" />
  return <Info className="h-4 w-4 text-gold-400" />
}

function Metric({ label, value, accent }: { label: string; value: string; accent: "gold" | "sell" | "warn" | "muted" }) {
  const color = accent === "sell" ? "text-sell" : accent === "warn" ? "text-warn" : accent === "gold" ? "text-gold-400" : "text-foreground"
  return (
    <Card>
      <CardContent className="py-4">
        <div className="label-xs">{label}</div>
        <div className={cn("num mt-2 text-lg font-bold", color)}>{value}</div>
      </CardContent>
    </Card>
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
