import { useState, type ReactNode } from "react"
import { Activity, CalendarRange, Layers3, ShieldCheck, TrendingUp } from "lucide-react"
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { Badge } from "@/components/ui/badge"
import { ActivityLogFeed } from "@/components/control/ActivityLogFeed"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useAnalytics } from "@/hooks/use-data"
import { cn } from "@/lib/utils"

export default function Analytics() {
  const [session, setSession] = useState("all")
  const [confirmation, setConfirmation] = useState("all")
  const [symbol, setSymbol] = useState("all")

  const query = useAnalytics({ session, confirmation_type: confirmation, symbol })
  const data = query.data
  const metrics = data?.metrics
  const charts = data?.charts ?? {
    cumulative_pips: [],
    session_performance: [],
    micro_confirmation_performance: [],
    win_loss_distribution: [],
    performance_by_bias: [],
    performance_by_period: [],
  }
  const breakdowns = data?.breakdowns ?? {
    session: [],
    setup_type: [],
    micro_confirmation: [],
    bias_gate: [],
    outcome_mix: [],
  }

  return (
    <div className="p-4 md:p-6 space-y-5">
      <div className="fixed inset-x-0 top-0 h-44 bg-glow-gold-top pointer-events-none" />

      <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div className="space-y-1">
          <div className="inline-flex items-center gap-2 rounded-full border border-gold-500/20 bg-gold-500/8 px-3 py-1 text-[11px] font-semibold text-gold-400">
            <TrendingUp className="h-3.5 w-3.5" />
            Replay Analytics
          </div>
          <h1 className="text-xl font-bold text-foreground md:text-2xl">Analytics</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">Supabase-backed replay and performance data visualized without changing the existing premium dashboard feel.</p>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <FilterPill icon={CalendarRange} label="Session" value={session} onChange={setSession} options={["all", "asia", "london", "new_york"]} />
          <FilterPill icon={ShieldCheck} label="Confirmation" value={confirmation} onChange={setConfirmation} options={["all", "liquidity_sweep_reclaim", "double_pattern", "unknown"]} />
          <FilterPill icon={Layers3} label="Symbol" value={symbol} onChange={setSymbol} options={["all", "XAUUSD"]} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-5">
        <Metric label="Total Trades" value={String(metrics?.total_trades ?? 0)} accent="gold" />
        <Metric label="Win Rate" value={`${metrics?.win_rate ?? 0}%`} accent="buy" />
        <Metric label="TP1 Hit Rate" value={`${metrics?.tp1_hit_rate ?? 0}%`} accent="gold" />
        <Metric label="Net Pips" value={`${(metrics?.net_pips ?? 0) > 0 ? "+" : ""}${metrics?.net_pips ?? 0}`} accent={(metrics?.net_pips ?? 0) >= 0 ? "buy" : "sell"} />
        <Metric label="Avg Pips / Trade" value={`${metrics?.avg_pips_per_trade ?? 0}`} accent="muted" />
      </div>

      {query.isLoading ? <State label="Loading analytics..." /> : null}
      {query.error instanceof Error ? <State label={query.error.message} tone="error" /> : null}

      {!query.isLoading && !(query.error instanceof Error) ? (
        <>
          <div className="grid grid-cols-1 gap-4 2xl:grid-cols-12">
            <ChartCard title="Cumulative Pips Curve" description="Replay growth across the filtered sample." className="2xl:col-span-7">
              <ChartWrap>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={charts.cumulative_pips}>
                    <defs>
                      <linearGradient id="pipsFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#D4AF37" stopOpacity={0.32} />
                        <stop offset="100%" stopColor="#D4AF37" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
                    <XAxis dataKey="label" stroke="#667085" tickLine={false} axisLine={false} fontSize={11} />
                    <YAxis stroke="#667085" tickLine={false} axisLine={false} fontSize={11} />
                    <Tooltip content={<ChartTooltip />} />
                    <Area type="monotone" dataKey="pips" stroke="#D4AF37" strokeWidth={2.5} fill="url(#pipsFill)" />
                  </AreaChart>
                </ResponsiveContainer>
              </ChartWrap>
            </ChartCard>

            <ChartCard title="Win / Loss Distribution" description="Closed replay outcomes." className="2xl:col-span-5">
              <ChartWrap>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={charts.win_loss_distribution} dataKey="value" cx="50%" cy="50%" innerRadius={64} outerRadius={96} paddingAngle={4}>
                      {charts.win_loss_distribution.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
                    </Pie>
                    <Tooltip content={<ChartTooltip />} />
                    <Legend verticalAlign="bottom" iconType="circle" wrapperStyle={{ fontSize: "12px" }} />
                  </PieChart>
                </ResponsiveContainer>
              </ChartWrap>
            </ChartCard>
          </div>

          <div className="grid grid-cols-1 gap-4 2xl:grid-cols-12">
            <ChartCard title="Session Performance" description="Trade count, win rate, and pip contribution by session." className="2xl:col-span-6">
              <ChartWrap>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={charts.session_performance}>
                    <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
                    <XAxis dataKey="name" stroke="#667085" tickLine={false} axisLine={false} fontSize={11} />
                    <YAxis stroke="#667085" tickLine={false} axisLine={false} fontSize={11} />
                    <Tooltip content={<ChartTooltip />} />
                    <Legend verticalAlign="top" height={20} wrapperStyle={{ fontSize: "12px" }} />
                    <Bar dataKey="net_pips" fill="#D4AF37" radius={[6, 6, 0, 0]} />
                    <Bar dataKey="trades" fill="#10B981" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartWrap>
            </ChartCard>

            <ChartCard title="Micro Confirmation Performance" description="Replay edge by confirmation type." className="2xl:col-span-6">
              <ChartWrap>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={charts.micro_confirmation_performance} layout="vertical" margin={{ left: 20 }}>
                    <CartesianGrid stroke="rgba(255,255,255,0.05)" horizontal={false} />
                    <XAxis type="number" stroke="#667085" tickLine={false} axisLine={false} fontSize={11} />
                    <YAxis type="category" dataKey="name" stroke="#667085" tickLine={false} axisLine={false} fontSize={11} width={110} />
                    <Tooltip content={<ChartTooltip />} />
                    <Bar dataKey="win_rate" fill="#10B981" radius={[0, 6, 6, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartWrap>
            </ChartCard>
          </div>

          <div className="grid grid-cols-1 gap-4 2xl:grid-cols-12">
            <ChartCard title="Performance by Bias" description="Which bias states deliver the cleanest results." className="2xl:col-span-6">
              <ChartWrap>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={charts.performance_by_bias}>
                    <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
                    <XAxis dataKey="name" stroke="#667085" tickLine={false} axisLine={false} fontSize={10} interval={0} angle={-10} textAnchor="end" height={50} />
                    <YAxis stroke="#667085" tickLine={false} axisLine={false} fontSize={11} />
                    <Tooltip content={<ChartTooltip />} />
                    <Bar dataKey="net_pips" fill="#D4AF37" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartWrap>
            </ChartCard>

            <ChartCard title="Performance by Month / Week" description="Distribution of trades and net pips over time." className="2xl:col-span-6">
              <ChartWrap>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={charts.performance_by_period}>
                    <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
                    <XAxis dataKey="label" stroke="#667085" tickLine={false} axisLine={false} fontSize={11} />
                    <YAxis stroke="#667085" tickLine={false} axisLine={false} fontSize={11} />
                    <Tooltip content={<ChartTooltip />} />
                    <Legend verticalAlign="top" height={20} wrapperStyle={{ fontSize: "12px" }} />
                    <Bar dataKey="net_pips" fill="#10B981" radius={[6, 6, 0, 0]} />
                    <Bar dataKey="trades" fill="#D4AF37" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartWrap>
            </ChartCard>
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <BreakdownTable title="Session Breakdown" subtitle="Session contribution to replay results." headers={["Session", "Trades", "Wins", "TP1", "Net Pips", "Avg Pips"]} rows={breakdowns.session.map((row) => [row.session, String(row.trades), String(row.wins), String(row.tp1), `${row.net_pips}`, `${row.avg_pips}`])} />
            <BreakdownTable title="Setup Type Breakdown" subtitle="Performance by setup model." headers={["Setup Type", "Trades", "Win Rate", "Net Pips"]} rows={breakdowns.setup_type.map((row) => [row.setup_type, String(row.trades), `${row.win_rate}%`, `${row.net_pips}`])} />
            <BreakdownTable title="Micro Confirmation Breakdown" subtitle="How confirmations are contributing to outcomes." headers={["Micro", "Trades", "Win Rate", "TP1 Rate", "Net Pips"]} rows={breakdowns.micro_confirmation.map((row) => [row.micro, String(row.trades), `${row.win_rate}%`, `${row.tp1_rate}%`, `${row.net_pips}`])} />
            <BreakdownTable title="Bias Gate Breakdown" subtitle="Performance by bias gate quality." headers={["Bias Gate", "Trades", "Win Rate", "Net Pips"]} rows={breakdowns.bias_gate.map((row) => [row.bias_gate, String(row.trades), `${row.win_rate}%`, `${row.net_pips}`])} />
          </div>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <CardTitle>Outcome Mix</CardTitle>
                  <CardDescription>Closed trade composition across replay results.</CardDescription>
                </div>
                <Badge variant="muted" className="text-[10px]">{breakdowns.outcome_mix.length} outcome states</Badge>
              </div>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4 pt-0">
              {breakdowns.outcome_mix.map((item) => (
                <div key={item.outcome} className="rounded-xl border border-ap-border bg-ap-surface/35 p-4">
                  <div className="label-xs">{item.outcome}</div>
                  <div className={cn("num mt-2 text-2xl font-bold", item.color)}>{item.trades}</div>
                  <div className="mt-1 text-xs text-muted-foreground">trades in this outcome bucket</div>
                </div>
              ))}
            </CardContent>
          </Card>

          <ActivityLogFeed />
        </>
      ) : null}
    </div>
  )
}

function FilterPill({ icon: Icon, label, value, onChange, options }: { icon: typeof Activity; label: string; value: string; onChange: (value: string) => void; options: string[] }) {
  return (
    <Card>
      <CardContent className="py-3">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-gold-400" />
          <div className="min-w-0 flex-1">
            <div className="label-xs">{label}</div>
            <select value={value} onChange={(e) => onChange(e.target.value)} className="mt-1 w-full bg-transparent text-xs font-semibold text-foreground outline-none">
              {options.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function Metric({ label, value, accent }: { label: string; value: string; accent: "gold" | "buy" | "sell" | "muted" }) {
  const color = accent === "buy" ? "text-buy" : accent === "sell" ? "text-sell" : accent === "gold" ? "text-gold-400" : "text-foreground"
  return (
    <Card>
      <CardContent className="py-4">
        <div className="label-xs">{label}</div>
        <div className={cn("num mt-2 text-lg font-bold", color)}>{value}</div>
      </CardContent>
    </Card>
  )
}

function ChartCard({ title, description, className, children }: { title: string; description: string; className?: string; children: ReactNode }) {
  return (
    <Card className={className}>
      <CardHeader className="pb-3">
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="pt-0">{children}</CardContent>
    </Card>
  )
}

function ChartWrap({ children }: { children: ReactNode }) {
  return <div className="h-[320px]">{children}</div>
}

function BreakdownTable({ title, subtitle, headers, rows }: { title: string; subtitle: string; headers: string[]; rows: string[][] }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle>{title}</CardTitle>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-ap-border">
                {headers.map((header) => <th key={header} className="px-3 py-2 text-left label-xs">{header}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => (
                <tr key={`${title}-${idx}`} className="border-b border-ap-border/50 last:border-0">
                  {row.map((value, valueIdx) => <td key={`${title}-${idx}-${valueIdx}`} className="px-3 py-3 text-[11px] text-foreground">{value}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ name?: string; value?: string | number; color?: string }>; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-ap-border bg-ap-card px-3 py-2 shadow-card">
      <div className="text-[11px] font-semibold text-foreground">{label}</div>
      <div className="mt-2 space-y-1">
        {payload.map((entry, index) => (
          <div key={`${entry.name}-${index}`} className="flex items-center gap-2 text-[11px] text-muted-foreground">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: entry.color }} />
            <span>{entry.name}</span>
            <span className="num font-semibold text-foreground">{entry.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function State({ label, tone = "muted" }: { label: string; tone?: "muted" | "error" }) {
  return (
    <Card>
      <CardContent className="py-12 text-center">
        <p className={cn("text-sm", tone === "error" ? "text-sell" : "text-muted-foreground")}>{label}</p>
      </CardContent>
    </Card>
  )
}
