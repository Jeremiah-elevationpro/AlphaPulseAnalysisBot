import { useState } from "react"
import { Plus, Crosshair, ArrowUpRight, ArrowDownRight, Trash2 } from "lucide-react"
import { motion, AnimatePresence } from "framer-motion"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"

type SetupType = "A" | "V" | "Gap"

interface Setup {
  id: number
  type: SetupType
  price: number
  sl: number
  tp: number
  notes: string
  createdAt: string
}

const EXAMPLE_SETUPS: Setup[] = [
  { id: 1, type: "A", price: 2310.0, sl: 2315.0, tp: 2280.0, notes: "Major resistance, H4 confirmed bearish", createdAt: "2h ago" },
  { id: 2, type: "V", price: 2220.0, sl: 2215.0, tp: 2260.0, notes: "Clean W-bottom, origin candle present", createdAt: "Yesterday" },
]

export default function ManualSetups() {
  const [setups, setSetups] = useState<Setup[]>(EXAMPLE_SETUPS)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ type: "A" as SetupType, price: "", sl: "", tp: "", notes: "" })

  const handleAdd = () => {
    if (!form.price) return
    const next: Setup = {
      id: Date.now(),
      type: form.type,
      price: parseFloat(form.price),
      sl: parseFloat(form.sl) || 0,
      tp: parseFloat(form.tp) || 0,
      notes: form.notes,
      createdAt: "Just now",
    }
    setSetups((prev) => [next, ...prev])
    setForm({ type: "A", price: "", sl: "", tp: "", notes: "" })
    setShowForm(false)
  }

  const handleDelete = (id: number) => {
    setSetups((prev) => prev.filter((s) => s.id !== id))
  }

  return (
    <div className="p-4 md:p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-foreground">Manual Setups</h2>
          <p className="text-sm text-muted-foreground">Add custom levels for manual tracking</p>
        </div>
        <Button onClick={() => setShowForm((v) => !v)} variant={showForm ? "outline" : "default"} size="sm">
          <Plus className="w-3.5 h-3.5" />
          {showForm ? "Cancel" : "Add Setup"}
        </Button>
      </div>

      {/* Add form */}
      <AnimatePresence>
        {showForm && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.22 }}
            className="overflow-hidden"
          >
            <Card className="border-gold-500/20 shadow-gold-xs">
              <div className="h-0.5 bg-gradient-to-r from-transparent via-gold-500/50 to-transparent" />
              <CardHeader>
                <CardTitle>New Setup</CardTitle>
                <CardDescription>Define your level parameters manually</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* Type selector */}
                <div className="space-y-1.5">
                  <label className="label-xs">Level Type</label>
                  <div className="flex gap-2">
                    {(["A", "V", "Gap"] as SetupType[]).map((t) => (
                      <button
                        key={t}
                        onClick={() => setForm((f) => ({ ...f, type: t }))}
                        className={cn(
                          "px-4 py-2 rounded-lg text-xs font-semibold border transition-all",
                          form.type === t
                            ? t === "A"
                              ? "bg-sell-dim border-sell-border text-sell"
                              : t === "V"
                              ? "bg-buy-dim border-buy-border text-buy"
                              : "bg-[rgba(212,175,55,0.1)] border-[rgba(212,175,55,0.25)] text-gold-400"
                            : "border-ap-border bg-ap-surface text-muted-foreground hover:text-foreground"
                        )}
                      >
                        {t === "A" ? "A-Level (SELL)" : t === "V" ? "V-Level (BUY)" : "Gap / FVG"}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div className="space-y-1.5">
                    <label className="label-xs">Entry Price</label>
                    <Input
                      placeholder="2287.50"
                      value={form.price}
                      onChange={(e) => setForm((f) => ({ ...f, price: e.target.value }))}
                      className="font-mono"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="label-xs">Stop Loss</label>
                    <Input
                      placeholder="2295.00"
                      value={form.sl}
                      onChange={(e) => setForm((f) => ({ ...f, sl: e.target.value }))}
                      className="font-mono"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="label-xs">Take Profit</label>
                    <Input
                      placeholder="2255.00"
                      value={form.tp}
                      onChange={(e) => setForm((f) => ({ ...f, tp: e.target.value }))}
                      className="font-mono"
                    />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label className="label-xs">Notes (optional)</label>
                  <Input
                    placeholder="Describe the setup context..."
                    value={form.notes}
                    onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
                  />
                </div>

                <div className="flex justify-end gap-2 pt-1">
                  <Button variant="outline" size="sm" onClick={() => setShowForm(false)}>
                    Cancel
                  </Button>
                  <Button size="sm" onClick={handleAdd} disabled={!form.price}>
                    Add Setup
                  </Button>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Setups list */}
      {setups.length === 0 ? (
        <EmptySetups />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <AnimatePresence initial={false}>
            {setups.map((s) => (
              <motion.div
                key={s.id}
                initial={{ opacity: 0, scale: 0.97 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95, height: 0 }}
                transition={{ duration: 0.2 }}
              >
                <SetupCard setup={s} onDelete={handleDelete} />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}

function SetupCard({ setup, onDelete }: { setup: Setup; onDelete: (id: number) => void }) {
  const isBuy = setup.type === "V"
  const badgeClass = setup.type === "A" ? "badge-a" : setup.type === "V" ? "badge-v" : "badge-gap"
  const rr = setup.sl && setup.tp && setup.price
    ? Math.abs(setup.tp - setup.price) / Math.abs(setup.sl - setup.price)
    : null

  return (
    <Card className="hover:border-ap-border-strong transition-all group">
      <CardContent className="pt-4 pb-4 space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2.5">
            <span className={cn("inline-flex items-center justify-center w-10 h-8 rounded-lg text-xs font-bold border", badgeClass)}>
              {setup.type}
            </span>
            <div>
              <div className="num text-lg font-bold text-foreground">
                {setup.price.toLocaleString("en-US", { minimumFractionDigits: 2 })}
              </div>
              <div className={cn("flex items-center gap-0.5 text-xs font-semibold", isBuy ? "text-buy" : "text-sell")}>
                {isBuy ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                {setup.type === "V" ? "BUY" : setup.type === "A" ? "SELL" : "BUY"}
              </div>
            </div>
          </div>
          <button
            onClick={() => onDelete(setup.id)}
            className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded-md hover:bg-sell-dim text-muted-foreground hover:text-sell"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>

        <Separator />

        <div className="grid grid-cols-3 gap-2">
          <div className="text-center">
            <div className="label-xs mb-1">SL</div>
            <div className="num text-xs font-semibold text-sell">{setup.sl || "—"}</div>
          </div>
          <div className="text-center">
            <div className="label-xs mb-1">TP</div>
            <div className="num text-xs font-semibold text-buy">{setup.tp || "—"}</div>
          </div>
          <div className="text-center">
            <div className="label-xs mb-1">R:R</div>
            <div className="num text-xs font-semibold text-gold-400">
              {rr ? `1:${rr.toFixed(1)}` : "—"}
            </div>
          </div>
        </div>

        {setup.notes && (
          <p className="text-xs text-muted-foreground bg-ap-surface rounded-md px-3 py-2 border border-ap-border leading-relaxed">
            {setup.notes}
          </p>
        )}

        <div className="flex items-center justify-between">
          <Badge variant="muted" className="text-[10px]">Manual</Badge>
          <span className="text-[10px] text-muted-foreground">{setup.createdAt}</span>
        </div>
      </CardContent>
    </Card>
  )
}

function EmptySetups() {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center py-20 gap-3">
        <div className="w-14 h-14 rounded-full bg-ap-surface border border-ap-border flex items-center justify-center">
          <Crosshair className="w-6 h-6 text-muted-foreground/40" />
        </div>
        <div className="text-center">
          <p className="text-sm font-semibold text-foreground">No manual setups yet</p>
          <p className="text-xs text-muted-foreground mt-1 max-w-xs">
            Add a custom level to track alongside the bot's automated detections
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
