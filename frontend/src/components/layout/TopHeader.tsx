import { useLocation } from "react-router-dom"
import { Bell, User, Activity, Sparkles } from "lucide-react"
import { motion } from "framer-motion"
import { StatusDot } from "@/components/ui/status-dot"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

const PAGE_META: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "FX Unfiltered Dashboard", subtitle: "Powered by AlphaPulse" },
  "/signals": { title: "Signals", subtitle: "Spencer is tracking confirmed market structures" },
  "/setups": { title: "Manual Setups", subtitle: "Operator-defined setups ready for Spencer tracking" },
  "/trades": { title: "Trades", subtitle: "Live and historical execution monitored by Spencer" },
  "/alerts": { title: "Alerts", subtitle: "Bot, trade, and system notifications in one feed" },
  "/analytics": { title: "Analytics", subtitle: "Replay and live performance powered by AlphaPulse" },
  "/settings": { title: "Settings", subtitle: "Spencer control, replay tools, and runtime preferences" },
}

export function TopHeader() {
  const location = useLocation()
  const meta = PAGE_META[location.pathname] ?? { title: "FX Unfiltered", subtitle: "Powered by AlphaPulse" }
  const now = new Date().toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  })

  return (
    <header className="z-10 flex h-14 flex-shrink-0 items-center border-b border-ap-border bg-ap-sidebar/70 backdrop-blur-md">
      <div className="flex items-center gap-2.5 px-4 lg:hidden">
        <div className="flex h-7 w-7 items-center justify-center rounded-md border border-gold-500/25 bg-gold-500/15">
          <Activity className="h-3.5 w-3.5 text-gold-300" />
        </div>
        <span className="text-sm font-bold text-foreground">FX Unfiltered</span>
      </div>

      <div className="hidden min-w-0 items-center gap-2 px-6 lg:flex">
        <motion.div
          key={location.pathname}
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
        >
          <h1 className="text-sm font-semibold leading-tight text-foreground">{meta.title}</h1>
          <p className="hidden text-xs leading-tight text-muted-foreground xl:block">{meta.subtitle}</p>
        </motion.div>
      </div>

      <div className="mx-4 hidden flex-1 overflow-hidden lg:flex">
        <div className="flex w-full items-center justify-center rounded-lg border border-ap-border bg-ap-surface/60 px-3 py-1.5">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5 text-purple-300" />
            <span className="font-medium text-foreground">Spencer is monitoring XAUUSD</span>
            <Badge variant="purple" className="text-[10px]">Powered by AlphaPulse</Badge>
          </div>
        </div>
      </div>

      <div className="ml-auto flex items-center gap-1.5 px-4">
        <span className="mr-3 hidden text-xs text-muted-foreground xl:block">{now}</span>
        <div className="mr-1 hidden items-center gap-1.5 rounded-full border border-ap-border bg-ap-surface/70 px-2.5 py-1 sm:flex">
          <StatusDot status="online" pulse size="xs" />
          <span className="text-[10px] font-semibold uppercase tracking-wide text-buy">Spencer Online</span>
        </div>
        <Button variant="ghost" size="icon" className="relative" aria-label="Alerts">
          <Bell className="h-4 w-4" />
          <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-gold-400" />
        </Button>
        <button className="flex h-8 w-8 items-center justify-center rounded-full border border-purple-400/25 bg-purple-500/12 transition-colors hover:bg-purple-500/18">
          <User className="h-3.5 w-3.5 text-gold-300" />
        </button>
      </div>
    </header>
  )
}
