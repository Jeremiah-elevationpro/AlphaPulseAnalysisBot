import { Bot, Cpu, RadioTower, Sparkles } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { StatusDot } from "@/components/ui/status-dot"
import { cn } from "@/lib/utils"
import type { BotStatusResponse } from "@/lib/api"

const STATUS_LABELS: Record<string, string> = {
  online: "Spencer Online",
  offline: "Spencer Offline",
  starting: "Spencer Starting",
  analyzing: "Spencer Analyzing",
  watching: "Spencer Watching",
  stopping: "Spencer Stopping",
  error: "Spencer Error",
  running_replay: "Spencer Running Replay",
  tracking_manual_setup: "Spencer Tracking Manual Setup",
}

function useHeartbeatAge(lastHeartbeatAt?: string | null): { ageMin: number; isStale: boolean } {
  if (!lastHeartbeatAt) return { ageMin: -1, isStale: false }
  const ageMin = (Date.now() - new Date(lastHeartbeatAt).getTime()) / 60000
  return { ageMin, isStale: ageMin > 3 }
}

export function SpencerStatus({ status }: { status?: BotStatusResponse }) {
  const hb = useHeartbeatAge(status?.data?.lastHeartbeatAt ?? status?.last_heartbeat_at)
  const isActive = ["online", "analyzing", "watching"].includes(status?.status ?? "")

  const tone =
    status?.status === "online" || status?.status === "watching"
      ? "buy"
      : status?.status === "analyzing" || status?.status === "running_replay" || status?.status === "starting"
      ? "gold"
      : status?.status === "error"
      ? "sell"
      : "purple"

  return (
    <Card glow className="overflow-hidden">
      <CardContent className="relative py-5">
        <div className="pointer-events-none absolute inset-y-0 right-0 w-1/2 bg-gradient-to-l from-purple-500/12 via-gold-500/5 to-transparent" />
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-2">
            <div className="inline-flex items-center gap-2 rounded-full border border-purple-400/25 bg-purple-500/12 px-3 py-1 text-[11px] font-semibold text-purple-200">
              <Sparkles className="h-3.5 w-3.5 text-gold-300" />
              Assistant Status
            </div>
            <div>
              <h3 className="text-lg font-bold text-foreground">Spencer is monitoring {status?.symbol ?? "XAUUSD"}</h3>
              <p className="text-sm text-muted-foreground">{status?.message ?? "Spencer is waiting for market confirmation"}</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatusPill icon={Bot} label="Status" value={STATUS_LABELS[status?.status ?? "offline"] ?? status?.status ?? "Offline"} tone={tone} />
            <StatusPill icon={Cpu} label="Mode" value={status?.strategy_mode ?? "hybrid"} tone="purple" />
            <StatusPill icon={RadioTower} label="Session" value={status?.session ?? "london"} tone="gold" />
            <div className="rounded-xl border border-ap-border bg-ap-surface/45 px-3 py-3">
              <div className="label-xs">Backend</div>
              <div className="mt-2 flex items-center gap-2 text-sm font-semibold text-foreground">
                <StatusDot status={status?.backend_connected ? "online" : "offline"} pulse={Boolean(status?.backend_connected)} />
                {status?.backend_connected ? "Connected" : "Unavailable"}
              </div>
            </div>
            <StatusPill
              icon={RadioTower}
              label="Bot Window"
              value={status?.data?.botWindowActive ? `Active until ${status?.data?.activeUntil ?? "19:00"}` : "Closed"}
              tone={status?.data?.botWindowActive ? "buy" : "purple"}
            />
            <StatusPill
              icon={Cpu}
              label="Session Block"
              value={status?.data?.sessionBlocking ? "Yes" : "No"}
              tone={status?.data?.sessionBlocking ? "sell" : "buy"}
            />
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <Badge variant={tone === "purple" ? "purple" : tone === "gold" ? "gold" : tone === "buy" ? "buy" : "sell"} className="text-[10px]">
            {STATUS_LABELS[status?.status ?? "offline"] ?? "Spencer Offline"}
          </Badge>
          <Badge variant="gold" className="text-[10px]">Powered by AlphaPulse</Badge>
          <Badge variant="outline" className="text-[10px]">Current Symbol: {status?.symbol ?? "XAUUSD"}</Badge>
          {isActive && hb.isStale && (
            <Badge variant="sell" className="text-[10px]">
              ⚠ Heartbeat stale ({Math.floor(hb.ageMin)}m ago)
            </Badge>
          )}
          {(status?.data?.lastScanResult) && (
            <Badge variant="outline" className="text-[10px]">{status.data.lastScanResult}</Badge>
          )}
          {status?.data?.instanceId && (
            <Badge variant="outline" className="text-[10px]">Instance {status.data.instanceId}</Badge>
          )}
          {status?.data?.lastScanNumber != null && (
            <Badge variant="outline" className="text-[10px]">Scan #{status.data.lastScanNumber}</Badge>
          )}
          {status?.data?.botWindowActive && status?.data?.sessionBlocking && (
            <Badge variant="sell" className="text-[10px]">
              Configuration error: session label is blocking active bot window.
            </Badge>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function StatusPill({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: typeof Bot
  label: string
  value: string
  tone: "gold" | "purple" | "buy" | "sell"
}) {
  const colorClass =
    tone === "buy"
      ? "border-buy-border bg-buy-dim text-buy"
      : tone === "sell"
      ? "border-sell-border bg-sell-dim text-sell"
      : tone === "gold"
      ? "border-gold-500/25 bg-gold-500/10 text-gold-300"
      : "border-purple-400/25 bg-purple-500/12 text-purple-200"

  return (
    <div className="rounded-xl border border-ap-border bg-ap-surface/45 px-3 py-3">
      <div className="label-xs">{label}</div>
      <div className="mt-2 flex items-center gap-2">
        <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg border", colorClass)}>
          <Icon className="h-4 w-4" />
        </div>
        <div className="text-sm font-semibold capitalize text-foreground">{value}</div>
      </div>
    </div>
  )
}
