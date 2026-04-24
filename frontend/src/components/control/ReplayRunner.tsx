import { useEffect, useMemo, useState } from "react"
import { Loader2, PlayCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { useReplayLatest, useReplayResults, useReplayStatus, useRunReplay } from "@/hooks/use-data"
import { useToast } from "@/components/ui/toast"
import { ReplayResults } from "./ReplayResults"

const MONTH_OPTIONS = [1, 2, 3, 4, 6]

export function ReplayRunner() {
  const [symbol, setSymbol] = useState("XAUUSD")
  const [months, setMonths] = useState(2)
  const [customMonths, setCustomMonths] = useState("")
  const [showTrades, setShowTrades] = useState(20)
  const [activeRunId, setActiveRunId] = useState<number | undefined>(undefined)

  const runReplay = useRunReplay()
  const replayLatest = useReplayLatest()
  const replayStatus = useReplayStatus(activeRunId)
  const replayResults = useReplayResults(activeRunId)
  const toast = useToast()

  useEffect(() => {
    const latestRunId = replayLatest.data?.runId
    const latestStatus = replayLatest.data?.status
    if (!latestRunId) return
    if (latestStatus === "running") {
      setActiveRunId(latestRunId)
      return
    }
    if (!activeRunId || activeRunId === latestRunId) {
      setActiveRunId(undefined)
    }
  }, [activeRunId, replayLatest.data?.runId, replayLatest.data?.status])

  useEffect(() => {
    if (replayStatus.data?.status === "completed") {
      toast.push({
        title: "Replay completed",
        description: "Spencer completed replay analysis",
        tone: "success",
      })
    }
  }, [replayStatus.data?.status, toast])

  const effectiveMonths = useMemo(() => {
    if (customMonths.trim()) {
      const parsed = Number(customMonths)
      return Number.isFinite(parsed) && parsed > 0 ? parsed : months
    }
    return months
  }, [customMonths, months])

  async function handleRunReplay() {
    if (!window.confirm(`Run replay for ${effectiveMonths} month(s) on ${symbol}?`)) return
    try {
      const result = await runReplay.mutateAsync({ symbol, months: effectiveMonths, showTrades })
      setActiveRunId(result.runId)
      toast.push({
        title: "Replay started",
        description: result.message,
        tone: "info",
      })
    } catch (err) {
      toast.push({
        title: "Replay failed",
        description: err instanceof Error ? err.message : "Unknown error",
        tone: "error",
      })
    }
  }

  const latestResult = activeRunId ? replayResults.data ?? replayLatest.data?.result ?? null : replayLatest.data?.result ?? null

  return (
    <div className="space-y-4">
      <Card glow>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle>Replay Runner</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                Spencer is ready to run replay analysis from the frontend.
              </p>
            </div>
            <Badge variant={replayStatus.data?.status === "running" ? "purple" : "gold"} className="text-[10px]">
              {replayStatus.data?.status ?? replayLatest.data?.status ?? "idle"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 pt-0">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Field label="Symbol">
              <Input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
            </Field>
            <Field label="Months">
              <select
                value={String(months)}
                onChange={(e) => setMonths(Number(e.target.value))}
                className="flex h-9 w-full rounded-md border border-ap-border bg-ap-surface px-3 py-1 text-sm text-foreground"
              >
                {MONTH_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option} month{option > 1 ? "s" : ""}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Custom Months">
              <Input value={customMonths} onChange={(e) => setCustomMonths(e.target.value)} placeholder="Optional" />
            </Field>
            <Field label="Show Trades">
              <Input
                className="num"
                value={String(showTrades)}
                onChange={(e) => setShowTrades(Number(e.target.value) || 20)}
              />
            </Field>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={handleRunReplay} disabled={runReplay.isPending} variant="purple">
              {runReplay.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
              Run Replay
            </Button>
            <div className="text-sm text-muted-foreground">
              {replayStatus.data?.status === "running"
                ? "Spencer is running replay analysis"
                : latestResult
                ? `Spencer found ${latestResult.activatedTrades} activated trades`
                : "No replay has been completed in this session yet."}
            </div>
          </div>
        </CardContent>
      </Card>

      <ReplayResults result={latestResult} />
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="label-xs">{label}</div>
      {children}
    </div>
  )
}
