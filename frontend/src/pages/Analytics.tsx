import { TrendingUp, Target, Award, Clock } from "lucide-react"
import { motion } from "framer-motion"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"

const PERF_STATS = [
  { label: "Win Rate", value: "68.4%", sub: "Last 30 trades", icon: Target, color: "text-buy" },
  { label: "Profit Factor", value: "2.34", sub: "Gross profit / loss", icon: TrendingUp, color: "text-gold-400" },
  { label: "Avg Win", value: "+$186", sub: "Per winning trade", icon: Award, color: "text-buy" },
  { label: "Avg Hold", value: "2h 18m", sub: "Average duration", icon: Clock, color: "text-muted-foreground" },
]

const TYPE_BREAKDOWN = [
  { type: "A", label: "A-Level (SELL)", wins: 8, total: 12, pnl: 624, badgeClass: "badge-a" },
  { type: "V", label: "V-Level (BUY)", wins: 10, total: 13, pnl: 847, badgeClass: "badge-v" },
  { type: "Gap", label: "Gap / FVG", wins: 3, total: 5, pnl: 213, badgeClass: "badge-gap" },
]

const MONTHLY = [
  { month: "Nov", pnl: 1240, trades: 18 },
  { month: "Dec", pnl: 890, trades: 14 },
  { month: "Jan", pnl: -320, trades: 11 },
  { month: "Feb", pnl: 1680, trades: 22 },
  { month: "Mar", pnl: 2140, trades: 28 },
  { month: "Apr", pnl: 847, trades: 9 },
]

const maxPnl = Math.max(...MONTHLY.map((m) => Math.abs(m.pnl)))

export default function Analytics() {
  return (
    <div className="p-4 md:p-6 space-y-5">
      <div>
        <h2 className="text-lg font-bold text-foreground">Analytics</h2>
        <p className="text-sm text-muted-foreground">Performance metrics and strategy statistics</p>
      </div>

      {/* Top stats */}
      <motion.div
        className="grid grid-cols-2 xl:grid-cols-4 gap-3"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
      >
        {PERF_STATS.map((s) => (
          <Card key={s.label}>
            <CardContent className="pt-4 pb-4">
              <div className="flex items-center gap-2 mb-2">
                <s.icon className={cn("w-4 h-4", s.color)} />
                <span className="label-xs">{s.label}</span>
              </div>
              <div className={cn("num text-2xl font-bold", s.color)}>{s.value}</div>
              <div className="text-xs text-muted-foreground mt-0.5">{s.sub}</div>
            </CardContent>
          </Card>
        ))}
      </motion.div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="bytype">By Level Type</TabsTrigger>
          <TabsTrigger value="monthly">Monthly P&L</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {/* Win rate gauge */}
            <Card>
              <CardHeader>
                <CardTitle>Win Rate Distribution</CardTitle>
                <CardDescription>Based on last 30 closed trades</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-center py-6">
                  <GaugeChart value={68.4} />
                </div>
                <div className="grid grid-cols-3 gap-3 mt-4">
                  <StatBox label="Wins" value="20" color="text-buy" />
                  <StatBox label="Losses" value="9" color="text-sell" />
                  <StatBox label="Break Even" value="1" color="text-muted-foreground" />
                </div>
              </CardContent>
            </Card>

            {/* Equity curve placeholder */}
            <Card>
              <CardHeader>
                <CardTitle>Equity Curve</CardTitle>
                <CardDescription>Cumulative P&L over time</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="relative h-48 rounded-lg bg-ap-surface border border-ap-border overflow-hidden flex items-center justify-center">
                  <div className="absolute inset-0">
                    <svg className="w-full h-full" viewBox="0 0 300 150" preserveAspectRatio="none">
                      <defs>
                        <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#10B981" stopOpacity="0.25" />
                          <stop offset="100%" stopColor="#10B981" stopOpacity="0" />
                        </linearGradient>
                      </defs>
                      <path
                        d="M0,130 L30,125 L60,110 L90,115 L110,100 L140,65 L170,55 L200,70 L230,40 L260,30 L300,18"
                        fill="none"
                        stroke="#10B981"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                      />
                      <path
                        d="M0,130 L30,125 L60,110 L90,115 L110,100 L140,65 L170,55 L200,70 L230,40 L260,30 L300,18 L300,150 L0,150Z"
                        fill="url(#equityGrad)"
                      />
                    </svg>
                  </div>
                  <p className="relative text-xs text-muted-foreground/40 z-10">
                    Connects to live data when backend is active
                  </p>
                </div>
                <div className="flex items-center justify-between mt-3 px-1">
                  <span className="num text-xs text-muted-foreground">$0</span>
                  <span className="num text-sm font-bold text-buy">+$5,204</span>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="bytype">
          <div className="space-y-3">
            {TYPE_BREAKDOWN.map((row) => (
              <Card key={row.type}>
                <CardContent className="pt-4 pb-4">
                  <div className="flex items-start justify-between gap-4 flex-wrap">
                    <div className="flex items-center gap-3">
                      <span className={cn("inline-flex items-center justify-center w-10 h-8 rounded-lg text-xs font-bold border", row.badgeClass)}>
                        {row.type}
                      </span>
                      <div>
                        <div className="text-sm font-semibold text-foreground">{row.label}</div>
                        <div className="text-xs text-muted-foreground">{row.total} trades · {row.wins} wins</div>
                      </div>
                    </div>

                    <div className="flex items-center gap-6">
                      <div>
                        <div className="label-xs">Win Rate</div>
                        <div className="num text-sm font-bold text-buy mt-0.5">
                          {((row.wins / row.total) * 100).toFixed(0)}%
                        </div>
                      </div>
                      <div>
                        <div className="label-xs">Net P&L</div>
                        <div className="num text-sm font-bold text-buy mt-0.5">+${row.pnl}</div>
                      </div>
                    </div>

                    <div className="w-full">
                      <div className="h-2 rounded-full bg-ap-border overflow-hidden">
                        <motion.div
                          initial={{ width: 0 }}
                          animate={{ width: `${(row.wins / row.total) * 100}%` }}
                          transition={{ duration: 0.7, ease: "easeOut" }}
                          className="h-full rounded-full bg-buy"
                        />
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="monthly">
          <Card>
            <CardHeader>
              <CardTitle>Monthly P&L</CardTitle>
              <CardDescription>Net profit/loss per month</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-end justify-between gap-2 h-40 mt-2">
                {MONTHLY.map((m, i) => {
                  const barH = (Math.abs(m.pnl) / maxPnl) * 100
                  const isPos = m.pnl >= 0
                  return (
                    <motion.div
                      key={m.month}
                      className="flex-1 flex flex-col items-center gap-1.5"
                      initial={{ opacity: 0, scaleY: 0 }}
                      animate={{ opacity: 1, scaleY: 1 }}
                      transition={{ delay: i * 0.06, duration: 0.4, origin: "bottom" }}
                    >
                      <span className={cn("num text-[10px] font-semibold", isPos ? "text-buy" : "text-sell")}>
                        {isPos ? "+" : ""}${Math.abs(m.pnl)}
                      </span>
                      <div className="w-full flex flex-col justify-end" style={{ height: "80px" }}>
                        <div
                          className={cn("w-full rounded-t transition-all", isPos ? "bg-buy/60" : "bg-sell/60")}
                          style={{ height: `${barH}%`, minHeight: "4px" }}
                        />
                      </div>
                      <span className="text-[10px] text-muted-foreground">{m.month}</span>
                    </motion.div>
                  )
                })}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function StatBox({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-ap-surface rounded-lg border border-ap-border p-3 text-center">
      <div className="label-xs">{label}</div>
      <div className={cn("num text-lg font-bold mt-1", color)}>{value}</div>
    </div>
  )
}

function GaugeChart({ value }: { value: number }) {
  const radius = 70
  const stroke = 10
  const normalizedR = radius - stroke / 2
  const circ = Math.PI * normalizedR
  const offset = circ - (value / 100) * circ

  return (
    <div className="relative flex items-center justify-center">
      <svg width="160" height="90" viewBox="0 0 160 90">
        {/* Background arc */}
        <path
          d="M 20,80 A 60,60 0 0,1 140,80"
          fill="none"
          stroke="hsl(228 22% 14%)"
          strokeWidth="10"
          strokeLinecap="round"
        />
        {/* Value arc */}
        <motion.path
          d="M 20,80 A 60,60 0 0,1 140,80"
          fill="none"
          stroke="#10B981"
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${circ}`}
          initial={{ strokeDashoffset: circ }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 1, ease: "easeOut" }}
        />
      </svg>
      <div className="absolute bottom-0 text-center">
        <div className="num text-2xl font-bold text-buy">{value}%</div>
        <div className="text-[10px] text-muted-foreground">Win Rate</div>
      </div>
    </div>
  )
}
