import { useLocation } from "react-router-dom"
import { Bell, User, TrendingUp, TrendingDown, Minus, Activity } from "lucide-react"
import { motion } from "framer-motion"
import { cn } from "@/lib/utils"
import { StatusDot } from "@/components/ui/status-dot"
import { Button } from "@/components/ui/button"

const PAGE_META: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Dashboard", subtitle: "XAUUSD analysis overview" },
  "/signals": { title: "Signals", subtitle: "Active and historical level signals" },
  "/setups": { title: "Manual Setups", subtitle: "Add and manage custom trade setups" },
  "/trades": { title: "Trades", subtitle: "Live and closed trade history" },
  "/alerts": { title: "Alerts", subtitle: "Notifications and system events" },
  "/analytics": { title: "Analytics", subtitle: "Performance metrics and statistics" },
  "/settings": { title: "Settings", subtitle: "Bot configuration and preferences" },
}

// Static market ticker — replaced with live data once backend connected
const TICKER_ITEMS = [
  { pair: "XAUUSD", price: "2,287.50", change: "+12.30", pct: "+0.54%", up: true },
  { pair: "DXY", price: "104.32", change: "-0.28", pct: "-0.27%", up: false },
  { pair: "US10Y", price: "4.285%", change: "+0.024", pct: "+0.56%", up: true },
  { pair: "XAUUSD", price: "2,287.50", change: "+12.30", pct: "+0.54%", up: true },
  { pair: "DXY", price: "104.32", change: "-0.28", pct: "-0.27%", up: false },
  { pair: "US10Y", price: "4.285%", change: "+0.024", pct: "+0.56%", up: true },
]

export function TopHeader() {
  const location = useLocation()
  const meta = PAGE_META[location.pathname] ?? { title: "AlphaPulse", subtitle: "" }
  const now = new Date().toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  })

  return (
    <header className="flex-shrink-0 h-14 flex items-center border-b border-ap-border bg-ap-sidebar/60 backdrop-blur-md z-10">
      {/* Mobile brand */}
      <div className="lg:hidden flex items-center gap-2.5 px-4">
        <div className="w-7 h-7 rounded-md bg-gold-500/15 border border-gold-500/25 flex items-center justify-center">
          <Activity className="w-3.5 h-3.5 text-gold-400" />
        </div>
        <span className="text-sm font-bold text-foreground">AlphaPulse</span>
      </div>

      {/* Desktop page title */}
      <div className="hidden lg:flex items-center gap-2 px-6 min-w-0">
        <motion.div
          key={location.pathname}
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
        >
          <h1 className="text-sm font-semibold text-foreground leading-tight">{meta.title}</h1>
          <p className="text-xs text-muted-foreground leading-tight hidden xl:block">{meta.subtitle}</p>
        </motion.div>
      </div>

      {/* Market ticker strip (desktop) */}
      <div className="hidden lg:flex flex-1 overflow-hidden mx-4">
        <div className="relative flex overflow-hidden rounded-lg border border-ap-border bg-ap-surface px-1">
          <motion.div
            className="flex items-center gap-0 py-0"
            animate={{ x: ["0%", "-50%"] }}
            transition={{ duration: 40, ease: "linear", repeat: Infinity }}
          >
            {TICKER_ITEMS.map((item, i) => (
              <TickerItem key={i} item={item} />
            ))}
          </motion.div>
        </div>
      </div>

      {/* Right controls */}
      <div className="flex items-center gap-1.5 px-4 ml-auto">
        {/* Date (desktop only) */}
        <span className="hidden xl:block text-xs text-muted-foreground mr-3">{now}</span>

        {/* System status pill */}
        <div className="hidden sm:flex items-center gap-1.5 rounded-full border border-ap-border bg-ap-surface px-2.5 py-1 mr-1">
          <StatusDot status="online" pulse size="xs" />
          <span className="text-[10px] font-semibold text-buy uppercase tracking-wide">Live</span>
        </div>

        {/* Notifications */}
        <Button variant="ghost" size="icon" className="relative" aria-label="Alerts">
          <Bell className="w-4 h-4" />
          <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-sell" />
        </Button>

        {/* User avatar */}
        <button className="w-8 h-8 rounded-full bg-gold-500/15 border border-gold-500/25 flex items-center justify-center hover:bg-gold-500/20 transition-colors">
          <User className="w-3.5 h-3.5 text-gold-400" />
        </button>
      </div>
    </header>
  )
}

function TickerItem({ item }: { item: (typeof TICKER_ITEMS)[0] }) {
  const Icon = item.up ? TrendingUp : item.pct === "+0.00%" ? Minus : TrendingDown
  return (
    <div className="flex items-center gap-2 px-4 py-2 border-r border-ap-border last:border-0 flex-shrink-0">
      <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest w-12">
        {item.pair}
      </span>
      <span className="num text-xs font-semibold text-foreground">{item.price}</span>
      <span className={cn("flex items-center gap-0.5 text-[10px] font-semibold num", item.up ? "text-buy" : "text-sell")}>
        <Icon className="w-2.5 h-2.5" />
        {item.pct}
      </span>
    </div>
  )
}
