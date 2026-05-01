import { Loader2, Play, RotateCcw, Square, Wifi } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { useBotStatus, useRestartBot, useStartBot, useStopBot } from "@/hooks/use-data"
import { useToast } from "@/components/ui/toast"

export function BotControlPanel() {
  const { data, isLoading, error, refetch } = useBotStatus()
  const startBot = useStartBot()
  const stopBot = useStopBot()
  const restartBot = useRestartBot()
  const toast = useToast()
  const statusData = data?.data
  const isRunning = data?.status === "online" || data?.status === "analyzing" || data?.status === "watching"
  const startLabel = isRunning ? "Spencer Running" : data?.status === "starting" ? "Starting Spencer" : "Start Spencer"
  const rejectReason = statusData?.lastRejectReason ?? null
  const duplicateLike = rejectReason === "all_alerted" || rejectReason === "duplicate"
  const lastScanDisplay = duplicateLike
    ? "All current setups already alerted"
    : (statusData?.watchlistCandidates ?? 0) > 0
    ? "Watchlist setups found"
    : statusData?.lastScanResult ?? data?.last_scan_result ?? "--"
  const telegramDisplay =
    statusData?.lastTelegramStatus === "success"
      ? "Success"
      : statusData?.lastTelegramStatus === "failed"
      ? "Failed"
      : statusData?.lastTelegramStatus === "none" && (statusData?.totalAlertsSent ?? 0) > 0
      ? "Success"
      : statusData?.lastTelegramStatus === "none"
      ? "Idle"
      : statusData?.lastTelegramStatus ?? "--"

  async function handleStart() {
    try {
      const result = await startBot.mutateAsync()
      toast.push({ title: "Spencer started", description: result.message, tone: "success" })
    } catch (err) {
      toast.push({ title: "Failed to start Spencer", description: err instanceof Error ? err.message : "Unknown error", tone: "error" })
    }
  }

  async function handleStop() {
    if (!window.confirm("Stop Spencer now?")) return
    try {
      const result = await stopBot.mutateAsync()
      toast.push({ title: "Spencer stopped", description: result.message, tone: "success" })
    } catch (err) {
      toast.push({ title: "Failed to stop Spencer", description: err instanceof Error ? err.message : "Unknown error", tone: "error" })
    }
  }

  async function handleRestart() {
    if (!window.confirm("Restart Spencer now?")) return
    try {
      const result = await restartBot.mutateAsync()
      toast.push({ title: "Spencer restarted", description: result.message, tone: "success" })
    } catch (err) {
      toast.push({ title: "Failed to restart Spencer", description: err instanceof Error ? err.message : "Unknown error", tone: "error" })
    }
  }

  return (
    <Card glow>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>Bot Control</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">AlphaPulse Engine Status and Spencer runtime controls.</p>
          </div>
          <Badge
            variant={
              data?.status === "online" || data?.status === "watching"
                ? "buy"
                : data?.status === "analyzing" || data?.status === "running_replay" || data?.status === "starting"
                ? "gold"
                : data?.status === "error"
                ? "sell"
                : "outline"
            }
            className="text-[10px]"
          >
            {data?.status ?? "offline"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <ActionButton
            onClick={handleStart}
            disabled={isRunning || startBot.isPending || stopBot.isPending || restartBot.isPending}
            variant={isRunning ? "secondary" : "buy"}
            loading={startBot.isPending}
            icon={Play}
            label={startLabel}
          />
          <ActionButton
            onClick={handleStop}
            disabled={startBot.isPending || stopBot.isPending || restartBot.isPending}
            variant="sell"
            loading={stopBot.isPending}
            icon={Square}
            label="Stop Spencer"
          />
          <ActionButton
            onClick={handleRestart}
            disabled={startBot.isPending || stopBot.isPending || restartBot.isPending}
            variant="purple"
            loading={restartBot.isPending}
            icon={RotateCcw}
            label="Restart Spencer"
          />
          <ActionButton
            onClick={() => refetch()}
            disabled={isLoading}
            variant="outline"
            loading={false}
            icon={Wifi}
            label="Check Spencer Status"
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Info label="Current Status" value={data?.status ?? "offline"} />
          <Info label="Last Started" value={formatDateTime(data?.last_started_at)} />
          <Info label="Last Stopped" value={formatDateTime(data?.last_stopped_at)} />
          <Info label="Strategy Mode" value={data?.strategy_mode ?? "hybrid"} />
          <Info label="Current Symbol" value={data?.symbol ?? "XAUUSD"} />
          <Info label="Market Session" value={formatSessionLabel(data?.session ?? statusData?.marketSession ?? statusData?.currentSession)} />
          <Info label="Operating Mode" value={statusData?.operatingMode === "24_7" ? "24/7 Always Active" : (statusData?.operatingMode ?? "24/7")} />
          <Info label="Scan Allowed" value={statusData?.scanAllowed ? "Yes" : "No"} />
          <Info label="Local Time" value={statusData?.localTime ?? "--"} />
          <Info label="Last Heartbeat" value={formatDateTime(statusData?.lastHeartbeatAt ?? data?.last_heartbeat_at)} />
          <Info label="Last Scan" value={formatDateTime(statusData?.lastScanAt ?? data?.last_scan_at)} />
          <Info label="Last Scan Number" value={statusData?.lastScanNumber != null ? String(statusData.lastScanNumber) : "--"} />
          <Info label="Last Scan Result" value={lastScanDisplay} />
          <Info label="Current Scan New Candidates" value={statusData?.watchlistCandidates != null ? String(statusData.watchlistCandidates) : "--"} />
          <Info label="Already Alerted / Duplicates" value={statusData?.dedupeRejections != null ? String(statusData.dedupeRejections) : "--"} />
          <Info label="Alerts Sent This Scan" value={statusData?.alertsSentThisScan != null ? String(statusData.alertsSentThisScan) : "--"} />
          <Info label="Total Alerts This Instance" value={statusData?.totalAlertsSent != null ? String(statusData.totalAlertsSent) : "--"} />
          <Info label="Telegram Status" value={telegramDisplay} />
          <Info
            label="Last Telegram Alert"
            value={
              statusData?.lastTelegramAlertType
                ? `${statusData.lastTelegramAlertType}${statusData.lastTelegramAlertTime ? ` · ${new Date(statusData.lastTelegramAlertTime).toLocaleString()}` : ""}`
                : "Idle"
            }
          />
          {statusData?.lastTelegramError && <Info label="Telegram Error" value={statusData.lastTelegramError} />}
          {statusData?.instanceId && <Info label="Active Instance ID" value={String(statusData.instanceId)} />}
          {statusData?.processId != null && <Info label="Process ID" value={String(statusData.processId)} />}
        </div>

        {statusData?.sessionBlocking && (
          <div className="rounded-xl border border-sell/25 bg-sell/8 px-4 py-3 text-sm">
            <div className="label-xs text-sell">Session Warning</div>
            <div className="mt-2 font-medium text-sell">Session blocking is active — check operating mode configuration.</div>
          </div>
        )}

        {(statusData?.lastError || statusData?.errorMessage || data?.last_error || data?.error_message) && (
          <div className="rounded-xl border border-sell/25 bg-sell/8 px-4 py-3 text-sm">
            <div className="label-xs text-sell">Last Error</div>
            <div className="mt-2 font-medium text-sell">
              {statusData?.lastError ?? statusData?.errorMessage ?? data?.last_error ?? data?.error_message}
            </div>
          </div>
        )}

        <div className="rounded-xl border border-ap-border bg-ap-surface/45 px-4 py-3 text-sm">
          <div className="label-xs">Connection Summary</div>
          <div className="mt-2 font-medium text-foreground">
            {error instanceof Error
              ? error.message
              : data?.backend_connected
              ? "Backend connection is healthy."
              : "Backend connection is unavailable."}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-ap-border bg-ap-surface/45 px-4 py-3">
      <div className="label-xs">{label}</div>
      <div className="mt-2 text-sm font-semibold capitalize text-foreground">{value}</div>
    </div>
  )
}

function ActionButton({
  onClick,
  disabled,
  variant,
  loading,
  icon: Icon,
  label,
}: {
  onClick: () => void
  disabled: boolean
  variant: "buy" | "sell" | "purple" | "outline" | "secondary"
  loading: boolean
  icon: typeof Play
  label: string
}) {
  return (
    <Button
      onClick={onClick}
      disabled={disabled}
      variant={variant}
      className={cn(
        "h-11 justify-start rounded-xl px-4 text-left",
        variant === "outline" ? "border-ap-border-strong bg-ap-surface/40 hover:bg-ap-surface/65" : ""
      )}
    >
      <span className="flex h-5 w-5 flex-shrink-0 items-center justify-center">
        {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4" />}
      </span>
      <span className="truncate font-semibold">{label}</span>
    </Button>
  )
}

function formatDateTime(value?: string | null) {
  if (!value) return "--"
  return new Date(value).toLocaleString()
}

function formatSessionLabel(value?: string | null) {
  if (!value || value === "off_session" || value === "quiet_session") return "Quiet Session"
  if (value === "overlap") return "Overlap"
  if (value === "london") return "London"
  if (value === "new_york") return "New York"
  if (value === "asia") return "Asia"
  return value.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
}
