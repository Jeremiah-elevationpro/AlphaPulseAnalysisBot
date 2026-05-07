import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { CheckCircle2, AlertTriangle, Info, X } from "lucide-react"
import { cn } from "@/lib/utils"

type ToastTone = "success" | "error" | "info"

type ToastItem = {
  id: number
  title: string
  description?: string
  tone: ToastTone
}

type ToastContextValue = {
  push: (toast: Omit<ToastItem, "id">) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const remove = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id))
  }, [])

  const push = useCallback((toast: Omit<ToastItem, "id">) => {
    const id = Date.now() + Math.floor(Math.random() * 1000)
    setItems((current) => [...current, { ...toast, id }])
    window.setTimeout(() => remove(id), 4200)
  }, [remove])

  const value = useMemo(() => ({ push }), [push])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed right-4 top-4 z-[70] flex w-full max-w-sm flex-col gap-2">
        <AnimatePresence>
          {items.map((item) => (
            <ToastCard key={item.id} item={item} onClose={() => remove(item.id)} />
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const value = useContext(ToastContext)
  if (!value) throw new Error("useToast must be used within ToastProvider")
  return value
}

function ToastCard({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  const toneStyles =
    item.tone === "success"
      ? "border-buy-border bg-buy-dim text-buy"
      : item.tone === "error"
      ? "border-sell-border bg-sell-dim text-sell"
      : "border-purple-400/30 bg-purple-500/14 text-purple-200"

  const Icon = item.tone === "success" ? CheckCircle2 : item.tone === "error" ? AlertTriangle : Info

  return (
    <motion.div
      initial={{ opacity: 0, y: -12, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -12, scale: 0.96 }}
      className={cn("pointer-events-auto rounded-xl border px-4 py-3 shadow-card backdrop-blur-md", toneStyles)}
    >
      <div className="flex items-start gap-3">
        <Icon className="mt-0.5 h-4 w-4 flex-shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">{item.title}</div>
          {item.description ? <div className="mt-1 text-xs opacity-80">{item.description}</div> : null}
        </div>
        <button type="button" onClick={onClose} className="opacity-70 transition-opacity hover:opacity-100">
          <X className="h-4 w-4" />
        </button>
      </div>
    </motion.div>
  )
}
