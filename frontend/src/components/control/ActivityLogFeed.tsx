import { useState } from "react"
import { Activity, AlertTriangle, Clock3, Radio, WifiOff } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useActivityLogs, useBotStatus, useReplayLogs, useRuntimeLogs, useTelegramLogs } from "@/hooks/use-data"
import { cn } from "@/lib/utils"

type FilterKey = "all" | "lifecycle" | "scan" | "telegram" | "errors" | "rejections" | "alerts"

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "lifecycle", label: "Lifecycle" },
  { key: "scan", label: "Scan" },
  { key: "telegram", label: "Telegram" },
  { key: "errors", label: "Errors" },
  { key: "rejections", label: "Rejections" },
  { key: "alerts", label: "Alerts" },
]

function classifyLine(line: string): Set<FilterKey> {
  const u = line.toUpperCase()
  const tags = new Set<FilterKey>(["all"])

  if (
    u.includes("BOT STARTED") ||
    u.includes("BOT STOPPED") ||
    u.includes("ANALYSIS PHASE") ||
    u.includes("WATCHLIST DEDUPE CLEARED")
  ) tags.add("lifecycle")

  if (
    u.includes("SCAN STARTED") ||
    u.includes("SCAN COMPLETE") ||
    u.includes("SCAN SUMMARY") ||
    u.includes("WATCHLIST LOOP STARTED") ||
    u.includes("WATCHLIST ALERT CALLING TELEGRAM")
  ) tags.add("scan")

  if (u.includes("TELEGRAM ")) tags.add("telegram")

  if (u.includes("FAILED") || u.includes("ERROR") || u.includes("MT5_NO_DATA")) tags.add("errors")

  if (
    u.includes("NO WATCHLIST SETUPS FOUND") ||
    u.includes("WATCHLIST ALERT SKIPPED") ||
    u.includes("SETUP REJECTED")
  ) tags.add("rejections")

  if (u.includes("WATCHLIST ALERT SENT") || u.includes("SETUP CANDIDATE FOUND")) tags.add("alerts")

  return tags
}

type EventTone = "lifecycle" | "success" | "error" | "warn" | "scan" | "muted"

function eventTone(line: string): EventTone {
  const u = line.toUpperCase()
  if (u.includes("FAILED") || u.includes("ERROR") || u.includes("MT5_NO_DATA")) return "error"
  if (u.includes("TELEGRAM ") && u.includes("SUCCESS")) return "success"
  if (u.includes("WATCHLIST ALERT SENT") || u.includes("BOT STARTED")) return "success"
  if (u.includes("BOT STOPPED") || u.includes("ANALYSIS PHASE STARTED")) return "warn"
  if (u.includes("ANALYSIS PHASE COMPLETE") || u.includes("WATCHLIST DEDUPE CLEARED")) return "lifecycle"
  if (u.includes("SCAN STARTED") || u.includes("SCAN COMPLETE") || u.includes("SCAN SUMMARY") || u.includes("WATCHLIST LOOP STARTED")) return "scan"
  return "muted"
}

function parseTimestamp(line: string): string {
  const m = line.match(/^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})]/)
  if (!m) return ""
  return m[1].slice(11)
}

function extractMessage(line: string): string {
  return line.replace(/^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}]\s*/, "")
}

function parseLogDate(line: string): Date | null {
  const m = line.match(/^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})]/)
  if (!m) return null
  return new Date(m[1].replace(" ", "T") + "Z")
}

function sourceLabel(source?: string) {
  if (!source) return "unknown"
  const normalized = source.replace(/\\/g, "/")
  const parts = normalized.split("/")
  return parts[parts.length - 1] || source
}

export function ActivityLogFeed() {
  const [filter, setFilter] = useState<FilterKey>("all")
  const botStatus = useBotStatus()
  const runtimeLogs = useRuntimeLogs(100)
  const activityLogs = useActivityLogs(30)
  const replayLogs = useReplayLogs(100)
  const telegramLogs = useTelegramLogs(100)

  const isRunning = ["online", "analyzing", "watching", "starting"].includes(botStatus.data?.status ?? "")
  const latestRuntimeLine = runtimeLogs.data?.events?.[0]?.line ?? ""
  const latestLogDate = parseLogDate(latestRuntimeLine)
  const ageMinutes = latestLogDate ? (Date.now() - latestLogDate.getTime()) / 60000 : null
  const isStale = isRunning && ageMinutes !== null && ageMinutes > 10

  const filteredRuntime = (runtimeLogs.data?.events ?? []).filter((event) => classifyLine(event.line).has(filter))

  return (
    <Card glow>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>Live Activity</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Runtime lifecycle, scan diagnostics, Telegram delivery, and replay visibility.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {isStale && (
              <Badge variant="warn" className="gap-1 text-[10px]">
                <WifiOff className="h-3 w-3" />
                Stale {ageMinutes !== null ? `(${Math.round(ageMinutes)}m)` : ""}
              </Badge>
            )}
            {isRunning && !isStale && runtimeLogs.data?.exists && (
              <Badge variant="default" className="gap-1 border-buy/25 bg-buy/15 text-[10px] text-buy">
                <Radio className="h-3 w-3" />
                Live
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>

      <CardContent className="pt-0">
        <Tabs defaultValue="live">
          <TabsList className="mb-4">
            <TabsTrigger value="live">Live Runtime</TabsTrigger>
            <TabsTrigger value="strategy">Strategy Detail</TabsTrigger>
            <TabsTrigger value="replay">Replay Logs</TabsTrigger>
            <TabsTrigger value="telegram">Telegram Logs</TabsTrigger>
          </TabsList>

          <TabsContent value="live" className="mt-0">
            <SourceRow source={runtimeLogs.data?.source} />
            {isStale && (
              <div className="mb-3 rounded-lg border border-gold-500/20 bg-gold-500/8 px-3 py-2 text-xs text-gold-300">
                Runtime log stale — Spencer may not be scanning.
              </div>
            )}
            <div className="mb-3 flex flex-wrap gap-1.5">
              {FILTERS.map((item) => (
                <button
                  key={item.key}
                  onClick={() => setFilter(item.key)}
                  className={cn(
                    "rounded-md border px-2.5 py-1 text-[11px] font-medium transition-colors",
                    filter === item.key
                      ? "border-gold-500/40 bg-gold-500/15 text-gold-300"
                      : "border-ap-border bg-ap-surface/40 text-muted-foreground hover:text-foreground"
                  )}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="space-y-2">
              {runtimeLogs.isLoading && <StateRow icon={Activity} label="Loading runtime log..." />}
              {runtimeLogs.error instanceof Error && (
                <StateRow icon={AlertTriangle} label={runtimeLogs.error.message} tone="error" />
              )}
              {!runtimeLogs.isLoading && !runtimeLogs.error && !runtimeLogs.data?.exists && (
                <StateRow icon={WifiOff} label="Runtime log not found — Spencer has not started yet." />
              )}
              {!runtimeLogs.isLoading && !runtimeLogs.error && runtimeLogs.data?.exists && filteredRuntime.length === 0 && (
                <StateRow icon={Clock3} label={`No "${filter}" runtime events yet.`} />
              )}
              {filteredRuntime.map((event) => (
                <LogRow
                  key={event.id}
                  tone={eventTone(event.line)}
                  timestamp={parseTimestamp(event.line)}
                  message={extractMessage(event.line)}
                />
              ))}
            </div>
          </TabsContent>

          <TabsContent value="strategy" className="mt-0">
            <SourceRow source={activityLogs.data?.source} />
            <PlainLogList
              isLoading={activityLogs.isLoading}
              error={activityLogs.error}
              emptyLabel="No strategy detail events logged yet."
              events={activityLogs.data?.events ?? []}
            />
          </TabsContent>

          <TabsContent value="replay" className="mt-0">
            <SourceRow source={replayLogs.data?.source} />
            <PlainLogList
              isLoading={replayLogs.isLoading}
              error={replayLogs.error}
              emptyLabel="No replay events logged yet."
              events={replayLogs.data?.events ?? []}
            />
          </TabsContent>

          <TabsContent value="telegram" className="mt-0">
            <SourceRow source={telegramLogs.data?.source} />
            <div className="space-y-2">
              {telegramLogs.isLoading && <StateRow icon={Activity} label="Loading Telegram diagnostics..." />}
              {telegramLogs.error instanceof Error && (
                <StateRow icon={AlertTriangle} label={telegramLogs.error.message} tone="error" />
              )}
              {!telegramLogs.isLoading && !telegramLogs.error && !telegramLogs.data?.events.length && (
                <StateRow icon={Clock3} label="No Telegram events logged yet." />
              )}
              {(telegramLogs.data?.events ?? []).map((event) => (
                <LogRow
                  key={event.id}
                  tone={eventTone(event.line)}
                  timestamp={parseTimestamp(event.line)}
                  message={extractMessage(event.line)}
                />
              ))}
            </div>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}

function SourceRow({ source }: { source?: string }) {
  return (
    <div className="mb-3 rounded-lg border border-ap-border bg-ap-surface/25 px-3 py-2 text-[11px] text-muted-foreground">
      Source: <span className="font-mono text-foreground">{sourceLabel(source)}</span>
    </div>
  )
}

function PlainLogList({
  isLoading,
  error,
  emptyLabel,
  events,
}: {
  isLoading: boolean
  error: unknown
  emptyLabel: string
  events: Array<{ id: number; line: string }>
}) {
  return (
    <div className="space-y-2">
      {isLoading && <StateRow icon={Activity} label="Loading log..." />}
      {error instanceof Error && <StateRow icon={AlertTriangle} label={error.message} tone="error" />}
      {!isLoading && !error && !events.length && <StateRow icon={Clock3} label={emptyLabel} />}
      {events.map((event) => (
        <div key={event.id} className="rounded-lg border border-ap-border bg-ap-surface/30 px-3 py-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
          {event.line}
        </div>
      ))}
    </div>
  )
}

function LogRow({
  tone,
  timestamp,
  message,
}: {
  tone: EventTone
  timestamp: string
  message: string
}) {
  const colors: Record<EventTone, string> = {
    success: "border-buy/25 bg-buy/8 text-buy",
    error: "border-sell/25 bg-sell/8 text-sell",
    warn: "border-gold-500/25 bg-gold-500/8 text-gold-300",
    lifecycle: "border-gold-500/20 bg-gold-500/6 text-gold-400",
    scan: "border-purple-400/20 bg-purple-500/8 text-purple-300",
    muted: "border-ap-border bg-ap-surface/30 text-muted-foreground",
  }

  return (
    <div className={cn("flex items-start gap-3 rounded-lg border px-3 py-2.5", colors[tone])}>
      {timestamp && <span className="mt-0.5 shrink-0 font-mono text-[10px] opacity-60">{timestamp}</span>}
      <span className="break-all text-xs leading-relaxed">{message}</span>
    </div>
  )
}

function StateRow({
  icon: Icon,
  label,
  tone = "muted",
}: {
  icon: typeof Activity
  label: string
  tone?: "muted" | "error"
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-4">
      <Icon className={cn("h-4 w-4 shrink-0", tone === "error" ? "text-sell" : "text-muted-foreground")} />
      <span className={cn("text-sm", tone === "error" ? "text-sell" : "text-muted-foreground")}>{label}</span>
    </div>
  )
}
