import {
  Activity,
  Zap,
  Target,
  TrendingUp,
  ArrowUpRight,
  ArrowDownRight,
  AlertTriangle,
  Info,
  BarChart3,
  Clock,
  Layers,
  Radio,
} from "lucide-react"
import { motion } from "framer-motion"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { StatusDot } from "@/components/ui/status-dot"
import { Separator } from "@/components/ui/separator"
import { Button } from "@/components/ui/button"
import { Link } from "react-router-dom"

// ── Static placeholder data ──────────────────────────────────────────
const STATS = [
  {
    label: "Bot Status",
    value: "Online",
    sub: "M30 → M15 · XAUUSD",
    icon: Activity,
    accent: "buy" as const,
    dot: true,
  },
  {
    label: "Today's Signals",
    value: "7",
    sub: "↑ 2 vs yesterday",
    icon: Zap,
    accent: "gold" as const,
  },
  {
    label: "Win Rate",
    value: "68.4%",
    sub: "+2.1% this week",
    icon: Target,
    accent: "buy" as const,
  },
  {
    label: "Today's P&L",
    value: "+$847",
    sub: "3 trades closed",
    icon: TrendingUp,
    accent: "buy" as const,
  },
]

const SIGNALS = [
  { type: "A", price: 2287.5, quality: 78, time: "14m ago", dir: "SELL", basis: "origin-based" },
  { type: "V", price: 2245.3, quality: 82, time: "1h ago", dir: "BUY", basis: "wick-based" },
  { type: "Gap", price: 2271.0, quality: 65, time: "2h ago", dir: "BUY", basis: "imbalance" },
  { type: "A", price: 2295.0, quality: 71, time: "3h ago", dir: "SELL", basis: "wick-based" },
  { type: "V", price: 2238.75, quality: 89, time: "5h ago", dir: "BUY", basis: "origin-based" },
]

const ALERTS = [
  { id: 1, sev: "warning", msg: "High-impact NFP news at 14:30 UTC", time: "2h" },
  { id: 2, sev: "info", msg: "Bot session started — M30→M15 pair active", time: "9h" },
]

const BIAS_ITEMS = [
  { tf: "H4", bias: "bearish", label: "H4 Trend" },
  { tf: "D1", bias: "neutral", label: "D1 Structure" },
  { tf: "W1", bias: "bullish", label: "W1 Bias" },
]

const stagger = {
  animate: { transition: { staggerChildren: 0.07 } },
}
const fadeUp = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.3 } },
}

export default function Dashboard() {
  return (
    <div className="p-4 md:p-6 space-y-5 bg-dot bg-dot-28">
      {/* Ambient top glow */}
      <div className="fixed inset-x-0 top-0 h-48 bg-glow-gold-top pointer-events-none" />

      {/* ── Stats Row ─────────────────────────────────────── */}
      <motion.div
        variants={stagger}
        initial="initial"
        animate="animate"
        className="grid grid-cols-2 xl:grid-cols-4 gap-3"
      >
        {STATS.map((s) => (
          <motion.div key={s.label} variants={fadeUp}>
            <StatCard stat={s} />
          </motion.div>
        ))}
      </motion.div>

      {/* ── Main Grid ─────────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* Chart / Market Panel (2/3) */}
        <motion.div
          variants={fadeUp}
          initial="initial"
          animate="animate"
          className="xl:col-span-2 space-y-4"
        >
          {/* Price display */}
          <Card glow className="overflow-hidden">
            <div className="h-1 bg-gradient-to-r from-gold-600 via-gold-400 to-gold-600 opacity-60" />
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Radio className="w-3.5 h-3.5 text-gold-400 animate-pulse" />
                  <CardTitle className="text-gold-400 tracking-wider font-mono">XAUUSD</CardTitle>
                  <Badge variant="gold" className="text-[10px]">LIVE</Badge>
                </div>
                <div className="flex items-center gap-2">
                  <span className="label-xs">Spread</span>
                  <span className="num text-xs text-muted-foreground">0.28</span>
                </div>
              </div>
              <div className="flex items-baseline gap-3 pt-1">
                <span className="num text-3xl font-bold text-foreground tracking-tight">
                  2,287.50
                </span>
                <div className="flex items-center gap-1 text-buy">
                  <ArrowUpRight className="w-4 h-4" />
                  <span className="num text-sm font-semibold">+12.30</span>
                  <span className="text-xs text-muted-foreground">(+0.54%)</span>
                </div>
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              {/* Chart placeholder */}
              <div className="relative h-44 rounded-lg border border-ap-border bg-ap-surface overflow-hidden flex items-center justify-center">
                <div className="absolute inset-0 bg-dot-grid opacity-40" />
                {/* Fake sparkline silhouette */}
                <svg
                  className="absolute inset-0 w-full h-full"
                  viewBox="0 0 400 176"
                  preserveAspectRatio="none"
                >
                  <defs>
                    <linearGradient id="lineGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#D4AF37" stopOpacity="0.3" />
                      <stop offset="100%" stopColor="#D4AF37" stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  <path
                    d="M0,120 C40,110 60,140 100,100 S160,60 200,80 S260,120 300,70 S360,40 400,55"
                    fill="none"
                    stroke="#D4AF37"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    opacity="0.5"
                  />
                  <path
                    d="M0,120 C40,110 60,140 100,100 S160,60 200,80 S260,120 300,70 S360,40 400,55 L400,176 L0,176 Z"
                    fill="url(#lineGrad)"
                  />
                </svg>
                <div className="relative flex flex-col items-center gap-1 text-center z-10">
                  <BarChart3 className="w-6 h-6 text-muted-foreground/40" />
                  <p className="text-xs text-muted-foreground/60">
                    Live chart connects when backend is active
                  </p>
                </div>
              </div>

              {/* Bias row */}
              <div className="flex items-center gap-3 mt-3">
                {BIAS_ITEMS.map((b) => (
                  <BiasTag key={b.tf} {...b} />
                ))}
                <div className="ml-auto flex items-center gap-1.5">
                  <Clock className="w-3 h-3 text-muted-foreground" />
                  <span className="text-xs text-muted-foreground">London session</span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Active Trades card */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Active Trades</CardTitle>
                <Badge variant="muted">0 open</Badge>
              </div>
            </CardHeader>
            <CardContent>
              <div className="flex flex-col items-center justify-center py-8 gap-2">
                <Layers className="w-8 h-8 text-muted-foreground/30" />
                <p className="text-sm text-muted-foreground">No active trades</p>
                <p className="text-xs text-muted-foreground/60">
                  Signals will appear here when confirmed
                </p>
              </div>
            </CardContent>
          </Card>
        </motion.div>

        {/* Right panel (1/3) */}
        <motion.div
          variants={fadeUp}
          initial="initial"
          animate="animate"
          transition={{ delay: 0.12 }}
          className="space-y-4"
        >
          {/* Recent Signals */}
          <Card className="flex flex-col">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Recent Signals</CardTitle>
                <Button variant="ghost" size="sm" asChild>
                  <Link to="/signals">View all</Link>
                </Button>
              </div>
            </CardHeader>
            <CardContent className="pt-2 space-y-1">
              {SIGNALS.map((sig, i) => (
                <SignalRow key={i} sig={sig} />
              ))}
            </CardContent>
          </Card>

          {/* Alerts */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Recent Alerts</CardTitle>
                <Badge variant="sell" className="text-[10px]">2 new</Badge>
              </div>
            </CardHeader>
            <CardContent className="pt-2 space-y-2">
              {ALERTS.map((a) => (
                <AlertRow key={a.id} alert={a} />
              ))}
            </CardContent>
          </Card>

          {/* Pipeline summary */}
          <Card>
            <CardHeader>
              <CardTitle>Pipeline · M30→M15</CardTitle>
            </CardHeader>
            <CardContent className="pt-3 space-y-3">
              {[
                { label: "Detected", a: 14, v: 11, gap: 4 },
                { label: "Survived filters", a: 8, v: 7, gap: 4 },
                { label: "Shortlisted", a: 3, v: 2, gap: 2 },
                { label: "Confirmed", a: 2, v: 1, gap: 1 },
              ].map((row) => (
                <div key={row.label} className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">{row.label}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-mono text-sell">A:{row.a}</span>
                      <span className="text-[10px] font-mono text-buy">V:{row.v}</span>
                      <span className="text-[10px] font-mono text-gold-400">G:{row.gap}</span>
                    </div>
                  </div>
                  <div className="h-1 rounded-full bg-ap-border overflow-hidden flex gap-0.5">
                    <div
                      className="h-full bg-sell/60 rounded-full"
                      style={{ width: `${(row.a / 14) * 100}%` }}
                    />
                    <div
                      className="h-full bg-buy/60 rounded-full"
                      style={{ width: `${(row.v / 11) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </div>
  )
}

// ── Sub-components ───────────────────────────────────────────────────

function StatCard({ stat }: { stat: (typeof STATS)[0] }) {
  const accentColor = {
    buy: "text-buy",
    gold: "text-gold-400",
    sell: "text-sell",
  }[stat.accent]

  return (
    <Card className="hover:border-ap-border-strong transition-all duration-200">
      <CardContent className="pt-5 pb-4">
        <div className="flex items-start justify-between mb-3">
          <div className={cn("w-9 h-9 rounded-lg flex items-center justify-center", "bg-ap-surface border border-ap-border")}>
            <stat.icon className={cn("w-4 h-4", accentColor)} />
          </div>
          {stat.dot && <StatusDot status="online" pulse size="sm" />}
        </div>
        <div className={cn("num text-2xl font-bold tracking-tight leading-tight", accentColor)}>
          {stat.value}
        </div>
        <div className="text-xs text-muted-foreground mt-0.5 leading-tight">{stat.sub}</div>
        <Separator className="mt-3 mb-2.5" />
        <div className="label-xs">{stat.label}</div>
      </CardContent>
    </Card>
  )
}

function SignalRow({ sig }: { sig: (typeof SIGNALS)[0] }) {
  const isBuy = sig.dir === "BUY"
  const badgeClass = sig.type === "A" ? "badge-a" : sig.type === "V" ? "badge-v" : "badge-gap"
  return (
    <div className="flex items-center gap-2.5 py-2 px-2 rounded-lg hover:bg-ap-surface transition-colors group cursor-pointer">
      <span
        className={cn(
          "inline-flex items-center justify-center w-8 h-6 rounded text-[10px] font-bold border flex-shrink-0",
          badgeClass
        )}
      >
        {sig.type}
      </span>
      <div className="flex-1 min-w-0">
        <div className="num text-xs font-semibold text-foreground">
          {sig.price.toLocaleString("en-US", { minimumFractionDigits: 2 })}
        </div>
        <div className="text-[10px] text-muted-foreground">{sig.basis}</div>
      </div>
      <div className="flex flex-col items-end gap-0.5">
        <div className={cn("flex items-center gap-0.5 text-[10px] font-semibold", isBuy ? "text-buy" : "text-sell")}>
          {isBuy ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
          {sig.dir}
        </div>
        <div className="text-[10px] text-muted-foreground">{sig.time}</div>
      </div>
      <div className="w-7 text-right">
        <span className="num text-[10px] font-semibold text-gold-400">Q{sig.quality}</span>
      </div>
    </div>
  )
}

function BiasTag({ tf, bias }: { tf: string; bias: string; label: string }) {
  const color =
    bias === "bullish" ? "text-buy" : bias === "bearish" ? "text-sell" : "text-muted-foreground"
  const bg =
    bias === "bullish" ? "bg-buy-dim border-buy-border" : bias === "bearish" ? "bg-sell-dim border-sell-border" : "bg-ap-surface border-ap-border"
  return (
    <div className={cn("flex items-center gap-1.5 rounded-md border px-2 py-1", bg)}>
      <span className="text-[10px] text-muted-foreground font-mono">{tf}</span>
      <span className={cn("text-[10px] font-semibold capitalize", color)}>{bias}</span>
    </div>
  )
}

function AlertRow({ alert }: { alert: (typeof ALERTS)[0] }) {
  const Icon = alert.sev === "warning" ? AlertTriangle : Info
  const color = alert.sev === "warning" ? "text-warn" : "text-muted-foreground"
  const bg = alert.sev === "warning" ? "bg-warn-dim" : "bg-ap-surface"
  return (
    <div className={cn("flex items-start gap-2.5 p-2.5 rounded-lg", bg)}>
      <Icon className={cn("w-3.5 h-3.5 mt-0.5 flex-shrink-0", color)} />
      <div className="flex-1 min-w-0">
        <p className="text-xs text-foreground leading-snug">{alert.msg}</p>
        <p className="text-[10px] text-muted-foreground mt-0.5">{alert.time} ago</p>
      </div>
    </div>
  )
}
