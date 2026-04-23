import { AlertTriangle, Info, XCircle, Bell, CheckCheck, X } from "lucide-react"
import { useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { cn } from "@/lib/utils"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"

interface Alert {
  id: number
  sev: "critical" | "warning" | "info"
  title: string
  detail: string
  time: string
  read: boolean
}

const INITIAL_ALERTS: Alert[] = [
  { id: 1, sev: "warning", title: "High-Impact News Scheduled", detail: "NFP data release at 14:30 UTC — bot may pause signal generation", time: "2h ago", read: false },
  { id: 2, sev: "critical", title: "Break Count Threshold Reached", detail: "A-level at 2310.0 reached break count 10 — level retired from pool", time: "4h ago", read: false },
  { id: 3, sev: "info", title: "Bot Session Started", detail: "M30→M15 pair activated. Detector initialized with 200 candles.", time: "9h ago", read: true },
  { id: 4, sev: "info", title: "New Signal Detected", detail: "V-level at 2238.75 passed all filters — quality 89/100", time: "5h ago", read: true },
  { id: 5, sev: "warning", title: "A/V Diversity Rule Applied", detail: "No A/V in shortlist — injected V-level from rejected candidates pool", time: "6h ago", read: true },
]

const NOTIF_SETTINGS = [
  { key: "telegram", label: "Telegram Signals", description: "Send confirmed signals to Telegram" },
  { key: "news", label: "News Warnings", description: "Alert before high-impact events" },
  { key: "system", label: "System Events", description: "Bot start, stop, errors" },
  { key: "breakout", label: "Level Breakouts", description: "Notify when a tracked level is broken" },
]

export default function Alerts() {
  const [alerts, setAlerts] = useState<Alert[]>(INITIAL_ALERTS)
  const [notifs, setNotifs] = useState<Record<string, boolean>>(
    Object.fromEntries(NOTIF_SETTINGS.map((n) => [n.key, true]))
  )

  const unread = alerts.filter((a) => !a.read).length

  const markAllRead = () => setAlerts((prev) => prev.map((a) => ({ ...a, read: true })))
  const dismiss = (id: number) => setAlerts((prev) => prev.filter((a) => a.id !== id))

  return (
    <div className="p-4 md:p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div>
            <h2 className="text-lg font-bold text-foreground">Alerts</h2>
            <p className="text-sm text-muted-foreground">System notifications and events</p>
          </div>
          {unread > 0 && <Badge variant="sell">{unread} unread</Badge>}
        </div>
        {unread > 0 && (
          <Button variant="ghost" size="sm" className="gap-1.5" onClick={markAllRead}>
            <CheckCheck className="w-3.5 h-3.5" />
            Mark all read
          </Button>
        )}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        {/* Alert list */}
        <div className="xl:col-span-2 space-y-2">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Recent</h3>
          <AnimatePresence initial={false}>
            {alerts.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-16 gap-3">
                  <Bell className="w-8 h-8 text-muted-foreground/30" />
                  <p className="text-sm text-muted-foreground">No alerts</p>
                </CardContent>
              </Card>
            ) : (
              alerts.map((alert) => (
                <motion.div
                  key={alert.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, x: 40, height: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <AlertCard alert={alert} onDismiss={() => dismiss(alert.id)} />
                </motion.div>
              ))
            )}
          </AnimatePresence>
        </div>

        {/* Notification settings */}
        <div>
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">
            Notification Preferences
          </h3>
          <Card>
            <CardContent className="pt-4 pb-4 space-y-0 divide-y divide-ap-border">
              {NOTIF_SETTINGS.map((n) => (
                <div key={n.key} className="flex items-center justify-between gap-3 py-3.5 first:pt-0 last:pb-0">
                  <div>
                    <p className="text-sm font-medium text-foreground">{n.label}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">{n.description}</p>
                  </div>
                  <Switch
                    checked={notifs[n.key]}
                    onCheckedChange={(v) => setNotifs((prev) => ({ ...prev, [n.key]: v }))}
                  />
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

const SEV_CONFIG = {
  critical: { Icon: XCircle, bg: "bg-sell-dim border-sell-border", iconClass: "text-sell", badge: "sell" as const },
  warning: { Icon: AlertTriangle, bg: "bg-warn-dim border-warn-border", iconClass: "text-warn", badge: "warn" as const },
  info: { Icon: Info, bg: "bg-ap-surface border-ap-border", iconClass: "text-muted-foreground", badge: "muted" as const },
}

function AlertCard({ alert, onDismiss }: { alert: Alert; onDismiss: () => void }) {
  const cfg = SEV_CONFIG[alert.sev]
  return (
    <div
      className={cn(
        "relative flex items-start gap-3 p-4 rounded-xl border transition-all",
        cfg.bg,
        alert.read ? "opacity-60" : ""
      )}
    >
      {!alert.read && (
        <span className="absolute top-3.5 right-10 w-1.5 h-1.5 rounded-full bg-sell" />
      )}
      <cfg.Icon className={cn("w-4 h-4 mt-0.5 flex-shrink-0", cfg.iconClass)} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-sm font-semibold text-foreground">{alert.title}</p>
          <Badge variant={cfg.badge} className="text-[10px] capitalize">{alert.sev}</Badge>
        </div>
        <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{alert.detail}</p>
        <p className="text-[10px] text-muted-foreground/60 mt-1.5">{alert.time}</p>
      </div>
      <button
        onClick={onDismiss}
        className="flex-shrink-0 p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-ap-surface transition-colors"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}
