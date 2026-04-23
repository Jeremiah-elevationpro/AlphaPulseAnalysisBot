import { useState } from "react"
import { Zap, ArrowUpRight, ArrowDownRight, Filter, Search, ChevronRight } from "lucide-react"
import { motion } from "framer-motion"
import { cn } from "@/lib/utils"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

type LevelType = "all" | "A" | "V" | "Gap"
type StatusFilter = "all" | "active" | "triggered" | "expired"

const SIGNALS = [
  { id: 1, type: "A", price: 2287.5, quality: 78, displacement: 45.2, touchCount: 2, breakCount: 0, basis: "origin-based", tf: "M30", dir: "SELL", status: "active", created: "14m ago" },
  { id: 2, type: "V", price: 2245.3, quality: 82, displacement: 62.5, touchCount: 1, breakCount: 0, basis: "wick-based", tf: "M30", dir: "BUY", status: "active", created: "1h ago" },
  { id: 3, type: "Gap", price: 2271.0, quality: 65, displacement: 38.0, touchCount: 3, breakCount: 1, basis: "imbalance", tf: "M30", dir: "BUY", status: "triggered", created: "2h ago" },
  { id: 4, type: "A", price: 2295.0, quality: 71, displacement: 52.1, touchCount: 2, breakCount: 0, basis: "wick-based", tf: "M30", dir: "SELL", status: "active", created: "3h ago" },
  { id: 5, type: "V", price: 2238.75, quality: 89, displacement: 74.3, touchCount: 1, breakCount: 0, basis: "origin-based", tf: "M30", dir: "BUY", status: "active", created: "5h ago" },
  { id: 6, type: "A", price: 2310.0, quality: 58, displacement: 31.0, touchCount: 4, breakCount: 2, basis: "wick-based", tf: "H1", dir: "SELL", status: "expired", created: "8h ago" },
  { id: 7, type: "V", price: 2220.0, quality: 74, displacement: 55.8, touchCount: 2, breakCount: 0, basis: "origin-based", tf: "H1", dir: "BUY", status: "active", created: "12h ago" },
] as const

const TF_OPTIONS = ["All", "M15", "M30", "H1", "H4"]
const TYPE_OPTIONS: { value: LevelType; label: string }[] = [
  { value: "all", label: "All Types" },
  { value: "A", label: "A-Level" },
  { value: "V", label: "V-Level" },
  { value: "Gap", label: "Gap / FVG" },
]
const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "triggered", label: "Triggered" },
  { value: "expired", label: "Expired" },
]

export default function Signals() {
  const [typeFilter, setTypeFilter] = useState<LevelType>("all")
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all")
  const [tfFilter, setTfFilter] = useState("All")

  const filtered = SIGNALS.filter((s) => {
    if (typeFilter !== "all" && s.type !== typeFilter) return false
    if (statusFilter !== "all" && s.status !== statusFilter) return false
    if (tfFilter !== "All" && s.tf !== tfFilter) return false
    return true
  })

  return (
    <div className="p-4 md:p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-foreground">Signals</h2>
          <p className="text-sm text-muted-foreground">{filtered.length} levels detected</p>
        </div>
        <Button variant="outline" size="sm" className="gap-2">
          <Filter className="w-3.5 h-3.5" />
          Export
        </Button>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="pt-4 pb-4">
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <Input placeholder="Search by price or basis..." className="pl-8" />
            </div>
            <FilterPills
              options={TYPE_OPTIONS.map((o) => o.value)}
              labels={TYPE_OPTIONS.map((o) => o.label)}
              active={typeFilter}
              onChange={(v) => setTypeFilter(v as LevelType)}
            />
            <FilterPills
              options={STATUS_OPTIONS.map((o) => o.value)}
              labels={STATUS_OPTIONS.map((o) => o.label)}
              active={statusFilter}
              onChange={(v) => setStatusFilter(v as StatusFilter)}
            />
          </div>

          {/* TF row */}
          <div className="flex items-center gap-1.5 mt-3">
            <span className="label-xs mr-1">Timeframe</span>
            {TF_OPTIONS.map((tf) => (
              <button
                key={tf}
                onClick={() => setTfFilter(tf)}
                className={cn(
                  "px-2.5 py-1 rounded text-xs font-semibold transition-colors",
                  tf === tfFilter
                    ? "bg-gold-500/15 text-gold-400 border border-gold-500/25"
                    : "text-muted-foreground hover:text-foreground border border-transparent hover:border-ap-border"
                )}
              >
                {tf}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Signals grid */}
      {filtered.length === 0 ? (
        <EmptyState />
      ) : (
        <motion.div
          className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3"
          initial="initial"
          animate="animate"
          variants={{ animate: { transition: { staggerChildren: 0.06 } } }}
        >
          {filtered.map((sig) => (
            <motion.div
              key={sig.id}
              variants={{ initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 } }}
            >
              <SignalCard sig={sig} />
            </motion.div>
          ))}
        </motion.div>
      )}
    </div>
  )
}

function SignalCard({ sig }: { sig: (typeof SIGNALS)[number] }) {
  const isBuy = sig.dir === "BUY"
  const typeClass = sig.type === "A" ? "badge-a" : sig.type === "V" ? "badge-v" : "badge-gap"
  const statusColor =
    sig.status === "active"
      ? "text-buy"
      : sig.status === "triggered"
      ? "text-gold-400"
      : "text-muted-foreground"

  return (
    <Card className="hover:border-ap-border-strong hover:-translate-y-0.5 transition-all duration-200 cursor-pointer group">
      <CardContent className="pt-4 pb-4 space-y-3">
        {/* Top row */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className={cn("inline-flex items-center justify-center w-9 h-7 rounded text-xs font-bold border", typeClass)}>
              {sig.type}
            </span>
            <div>
              <div className="num text-base font-bold text-foreground leading-tight">
                {sig.price.toLocaleString("en-US", { minimumFractionDigits: 2 })}
              </div>
              <div className="text-[10px] text-muted-foreground">{sig.basis}</div>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1">
            <div className={cn("flex items-center gap-0.5 text-xs font-semibold", isBuy ? "text-buy" : "text-sell")}>
              {isBuy ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
              {sig.dir}
            </div>
            <span className={cn("text-[10px] font-semibold capitalize", statusColor)}>{sig.status}</span>
          </div>
        </div>

        {/* Quality bar */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="label-xs">Quality Score</span>
            <span className="num text-xs font-bold text-gold-400">{sig.quality} / 100</span>
          </div>
          <div className="h-1.5 rounded-full bg-ap-border overflow-hidden">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${sig.quality}%` }}
              transition={{ duration: 0.6, ease: "easeOut", delay: 0.1 }}
              className={cn(
                "h-full rounded-full",
                sig.quality >= 80 ? "bg-buy" : sig.quality >= 60 ? "bg-gold-500" : "bg-muted-foreground"
              )}
            />
          </div>
        </div>

        {/* Meta grid */}
        <div className="grid grid-cols-3 gap-2 pt-0.5">
          <MetaCell label="Disp" value={`${sig.displacement.toFixed(0)}p`} />
          <MetaCell label="TC" value={String(sig.touchCount)} />
          <MetaCell label="BC" value={String(sig.breakCount)} highlight={sig.breakCount > 3} />
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between pt-1 border-t border-ap-border">
          <div className="flex items-center gap-1.5">
            <Badge variant="muted" className="text-[10px] py-0">{sig.tf}</Badge>
          </div>
          <span className="text-[10px] text-muted-foreground">{sig.created}</span>
          <ChevronRight className="w-3.5 h-3.5 text-muted-foreground/40 group-hover:text-muted-foreground transition-colors" />
        </div>
      </CardContent>
    </Card>
  )
}

function MetaCell({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="bg-ap-surface rounded-md px-2 py-1.5 text-center">
      <div className="label-xs">{label}</div>
      <div className={cn("num text-xs font-semibold mt-0.5", highlight ? "text-sell" : "text-foreground")}>
        {value}
      </div>
    </div>
  )
}

function FilterPills({
  options,
  labels,
  active,
  onChange,
}: {
  options: string[]
  labels: string[]
  active: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex items-center gap-1 bg-ap-surface rounded-lg border border-ap-border p-1">
      {options.map((opt, i) => (
        <button
          key={opt}
          onClick={() => onChange(opt)}
          className={cn(
            "px-2.5 py-1 rounded-md text-xs font-medium transition-all duration-150",
            opt === active
              ? "bg-ap-card text-foreground border border-ap-border"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          {labels[i]}
        </button>
      ))}
    </div>
  )
}

function EmptyState() {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center py-16 gap-3">
        <div className="w-12 h-12 rounded-full bg-ap-surface border border-ap-border flex items-center justify-center">
          <Zap className="w-5 h-5 text-muted-foreground/40" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium text-foreground">No signals match</p>
          <p className="text-xs text-muted-foreground mt-1">Try adjusting the filters</p>
        </div>
      </CardContent>
    </Card>
  )
}
