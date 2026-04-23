import { ArrowUpRight, ArrowDownRight } from "lucide-react"
import { motion } from "framer-motion"
import { cn } from "@/lib/utils"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

const TRADES = [
  { id: "T-001", dir: "BUY", type: "V", entry: 2245.3, sl: 2238.0, tp: 2280.0, close: 2278.5, pnl: 132.4, pips: 33.2, rr: "1:4.4", status: "closed", opened: "Apr 23, 09:14", closed: "Apr 23, 11:52" },
  { id: "T-002", dir: "SELL", type: "A", entry: 2287.5, sl: 2294.0, tp: 2252.0, close: 2265.0, pnl: 224.8, pips: 22.5, rr: "1:3.5", status: "closed", opened: "Apr 23, 07:30", closed: "Apr 23, 10:18" },
  { id: "T-003", dir: "BUY", type: "Gap", entry: 2271.0, sl: 2263.0, tp: 2295.0, close: null, pnl: null, pips: null, rr: "1:3.0", status: "open", opened: "Apr 23, 12:05", closed: null },
  { id: "T-004", dir: "SELL", type: "A", entry: 2295.0, sl: 2301.0, tp: 2265.0, close: 2301.5, pnl: -63.2, pips: -6.5, rr: "1:5.0", status: "closed", opened: "Apr 22, 16:00", closed: "Apr 22, 18:30" },
  { id: "T-005", dir: "BUY", type: "V", entry: 2238.75, sl: 2231.0, tp: 2270.0, close: 2270.0, pnl: 313.0, pips: 31.25, rr: "1:4.0", status: "closed", opened: "Apr 22, 11:00", closed: "Apr 22, 15:45" },
] as const

const STATS = [
  { label: "Total Trades", value: "5", sub: "This week" },
  { label: "Win Rate", value: "75%", sub: "3 of 4 closed", color: "text-buy" },
  { label: "Net P&L", value: "+$607.0", sub: "Closed trades", color: "text-buy" },
  { label: "Avg R:R", value: "1:4.0", sub: "Realized", color: "text-gold-400" },
]

export default function Trades() {
  const closed = TRADES.filter((t) => t.status === "closed")
  const open = TRADES.filter((t) => t.status === "open")

  return (
    <div className="p-4 md:p-6 space-y-5">
      <div>
        <h2 className="text-lg font-bold text-foreground">Trades</h2>
        <p className="text-sm text-muted-foreground">Live and closed trade history</p>
      </div>

      {/* Stats */}
      <motion.div
        className="grid grid-cols-2 xl:grid-cols-4 gap-3"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {STATS.map((s) => (
          <Card key={s.label}>
            <CardContent className="pt-4 pb-4">
              <div className="label-xs mb-2">{s.label}</div>
              <div className={cn("num text-xl font-bold text-foreground", s.color)}>{s.value}</div>
              <div className="text-xs text-muted-foreground mt-0.5">{s.sub}</div>
            </CardContent>
          </Card>
        ))}
      </motion.div>

      {/* Open trades */}
      {open.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-buy animate-pulse" />
            Open Trades ({open.length})
          </h3>
          {open.map((t) => (
            <TradeRow key={t.id} trade={t} />
          ))}
        </div>
      )}

      {/* Closed trades */}
      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-foreground">Closed Trades ({closed.length})</h3>
        <Card>
          {/* Table header */}
          <div className="grid grid-cols-[2fr_1fr_1fr_1fr_1fr_1fr_1fr] gap-2 px-4 py-2.5 border-b border-ap-border">
            {["ID", "Type", "Dir", "Entry", "Close", "P&L", "Status"].map((h) => (
              <div key={h} className="label-xs">{h}</div>
            ))}
          </div>
          {/* Rows */}
          {closed.map((t, i) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.04 }}
              className="grid grid-cols-[2fr_1fr_1fr_1fr_1fr_1fr_1fr] gap-2 px-4 py-3 border-b border-ap-border/50 last:border-0 hover:bg-ap-surface transition-colors items-center"
            >
              <div className="num text-xs text-muted-foreground">{t.id}</div>
              <div>
                <span className={cn("inline-flex items-center justify-center px-1.5 py-0.5 rounded text-[10px] font-bold border", t.type === "A" ? "badge-a" : t.type === "V" ? "badge-v" : "badge-gap")}>
                  {t.type}
                </span>
              </div>
              <div className={cn("flex items-center gap-0.5 text-xs font-semibold", t.dir === "BUY" ? "text-buy" : "text-sell")}>
                {t.dir === "BUY" ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                {t.dir}
              </div>
              <div className="num text-xs text-foreground">{t.entry.toFixed(2)}</div>
              <div className="num text-xs text-foreground">{t.close?.toFixed(2) ?? "—"}</div>
              <div className={cn("num text-xs font-semibold", t.pnl != null ? (t.pnl >= 0 ? "text-buy" : "text-sell") : "text-muted-foreground")}>
                {t.pnl != null ? `${t.pnl >= 0 ? "+" : ""}$${Math.abs(t.pnl).toFixed(0)}` : "—"}
              </div>
              <div>
                <Badge variant={t.pnl != null && t.pnl >= 0 ? "buy" : "sell"} className="text-[10px]">
                  {t.pnl != null && t.pnl >= 0 ? "Win" : "Loss"}
                </Badge>
              </div>
            </motion.div>
          ))}
        </Card>
      </div>
    </div>
  )
}

function TradeRow({ trade }: { trade: (typeof TRADES)[number] }) {
  return (
    <Card className="border-buy-border bg-buy-dim/30">
      <CardContent className="pt-3 pb-3">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-buy animate-pulse" />
              <span className="text-xs font-semibold text-buy">LIVE</span>
            </div>
            <span className={cn("inline-flex items-center justify-center px-1.5 py-0.5 rounded text-[10px] font-bold border", trade.type === "V" ? "badge-v" : "badge-a")}>
              {trade.type}
            </span>
            <div className={cn("flex items-center gap-0.5 text-xs font-semibold", trade.dir === "BUY" ? "text-buy" : "text-sell")}>
              {trade.dir === "BUY" ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
              {trade.dir}
            </div>
          </div>
          <div className="flex items-center gap-6">
            <MetaItem label="Entry" value={trade.entry.toFixed(2)} />
            <MetaItem label="SL" value={trade.sl.toFixed(2)} valueClass="text-sell" />
            <MetaItem label="TP" value={trade.tp.toFixed(2)} valueClass="text-buy" />
            <MetaItem label="R:R" value={trade.rr} valueClass="text-gold-400" />
          </div>
          <span className="text-[10px] text-muted-foreground">{trade.opened}</span>
        </div>
      </CardContent>
    </Card>
  )
}

function MetaItem({ label, value, valueClass = "text-foreground" }: { label: string; value: string; valueClass?: string }) {
  return (
    <div>
      <div className="label-xs">{label}</div>
      <div className={cn("num text-xs font-semibold mt-0.5", valueClass)}>{value}</div>
    </div>
  )
}
