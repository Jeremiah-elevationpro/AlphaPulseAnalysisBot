import {
  useEffect,
  useMemo,
  useState,
  type ComponentType,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react"
import {
  ArrowDownRight,
  ArrowUpRight,
  Bell,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  Eye,
  Flag,
  Layers3,
  Plus,
  Radio,
  ShieldCheck,
  Sparkles,
  Target,
} from "lucide-react"
import { motion, AnimatePresence } from "framer-motion"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { useCreateSetup, useSetups, useUpdateSetup } from "@/hooks/use-data"

type Direction = "BUY" | "SELL"
type TimeframePair = "M30 -> M15" | "H1 -> M15" | "H4 -> H1"
type Bias = "bullish strong" | "bullish moderate" | "bearish strong" | "bearish moderate" | "neutral"
type ConfirmationType =
  | "liquidity_sweep_reclaim"
  | "engulfing_reclaim"
  | "structure_reclaim"
  | "manual_discretionary"
type Session = "asia" | "london" | "new_york" | "off_session" | "quiet_session"
type ActivationMode =
  | "touch_activation"
  | "rejection_then_revisit"
  | "manual_activation"
  | "pending_order_ready"
type ManualSetupStatus =
  | "draft"
  | "watching"
  | "pending-order-ready"
  | "activated"
  | "TP1 hit"
  | "BE protected"
  | "TP2 hit"
  | "TP3 hit"
  | "stopped out"
  | "closed manually"
  | "expired"

interface ManualSetup {
  id: number
  symbol: string
  direction: Direction
  timeframePair: TimeframePair
  entryPrice: number
  stopLoss: number
  tp1: number
  tp2: number
  tp3: number
  bias: Bias
  confirmationType: ConfirmationType
  session: Session
  notes: string
  activationMode: ActivationMode
  moveSlToBeAfterTp1: boolean
  enableTelegramAlerts: boolean
  highPriority: boolean
  status: ManualSetupStatus
  trackingEnabled: boolean
  trackingStatus: string
  telegramAlertSent: boolean
  telegramError?: string | null
  createdAt: string
  updatedAt: string
}

type SetupForm = {
  symbol: string
  direction: Direction
  timeframePair: TimeframePair
  entryPrice: string
  stopLoss: string
  tp1: string
  tp2: string
  tp3: string
  bias: Bias
  confirmationType: ConfirmationType
  session: Session
  notes: string
  activationMode: ActivationMode
  moveSlToBeAfterTp1: boolean
  enableTelegramAlerts: boolean
  highPriority: boolean
}

const TIMEFRAME_OPTIONS: TimeframePair[] = ["M30 -> M15", "H1 -> M15", "H4 -> H1"]
const BIAS_OPTIONS: Bias[] = ["bullish strong", "bullish moderate", "bearish strong", "bearish moderate", "neutral"]
const CONFIRMATION_OPTIONS: ConfirmationType[] = [
  "liquidity_sweep_reclaim",
  "engulfing_reclaim",
  "structure_reclaim",
  "manual_discretionary",
]
const SESSION_OPTIONS: Session[] = ["asia", "london", "new_york", "quiet_session", "off_session"]
const ACTIVATION_OPTIONS: ActivationMode[] = [
  "touch_activation",
  "rejection_then_revisit",
  "manual_activation",
  "pending_order_ready",
]

const STATUS_ORDER: ManualSetupStatus[] = [
  "draft",
  "watching",
  "pending-order-ready",
  "activated",
  "TP1 hit",
  "BE protected",
  "TP2 hit",
  "TP3 hit",
  "stopped out",
  "closed manually",
  "expired",
]

const STATUS_CFG: Record<ManualSetupStatus, { variant: "gold" | "buy" | "sell" | "warn" | "muted" | "outline"; glow: string }> = {
  draft: { variant: "muted", glow: "border-ap-border" },
  watching: { variant: "outline", glow: "border-gold-500/25" },
  "pending-order-ready": { variant: "gold", glow: "border-gold-500/35" },
  activated: { variant: "buy", glow: "border-buy/30" },
  "TP1 hit": { variant: "buy", glow: "border-buy/35" },
  "BE protected": { variant: "gold", glow: "border-gold-500/35" },
  "TP2 hit": { variant: "buy", glow: "border-buy/35" },
  "TP3 hit": { variant: "buy", glow: "border-buy/35" },
  "stopped out": { variant: "sell", glow: "border-sell/35" },
  "closed manually": { variant: "warn", glow: "border-warn/35" },
  expired: { variant: "muted", glow: "border-ap-border" },
}

const DEFAULT_FORM: SetupForm = {
  symbol: "XAUUSD",
  direction: "SELL",
  timeframePair: "M30 -> M15",
  entryPrice: "4748.26",
  stopLoss: "4760.80",
  tp1: "4732.40",
  tp2: "4718.60",
  tp3: "4701.25",
  bias: "bearish strong",
  confirmationType: "liquidity_sweep_reclaim",
  session: "london",
  notes: "Gap revisit into London sell-side flow. Watching for manual handoff into bot tracking.",
  activationMode: "pending_order_ready",
  moveSlToBeAfterTp1: true,
  enableTelegramAlerts: true,
  highPriority: true,
}

export default function ManualSetups() {
  const setupsQuery = useSetups()
  const createSetup = useCreateSetup()
  const updateSetup = useUpdateSetup()
  const [form, setForm] = useState<SetupForm>(DEFAULT_FORM)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  const preview = useMemo(() => buildPreview(form), [form])
  const setups = useMemo<ManualSetup[]>(
    () =>
      (setupsQuery.data?.setups ?? []).map((setup) => ({
        id: setup.id,
        symbol: setup.symbol,
        direction: setup.direction,
        timeframePair: (setup.timeframe_pair as TimeframePair) ?? "M30 -> M15",
        entryPrice: setup.entry_price,
        stopLoss: setup.stop_loss,
        tp1: setup.tp1,
        tp2: setup.tp2 ?? 0,
        tp3: setup.tp3 ?? 0,
        bias: (setup.bias as Bias) ?? "neutral",
        confirmationType: (setup.confirmation_type as ConfirmationType) ?? "manual_discretionary",
        session: (setup.session as Session) ?? "off_session",
        notes: setup.notes ?? "",
        activationMode: (setup.activation_mode as ActivationMode) ?? "manual_activation",
        moveSlToBeAfterTp1: setup.move_sl_to_be_after_tp1,
        enableTelegramAlerts: setup.enable_telegram_alerts,
        highPriority: setup.high_priority,
        status: (setup.status as ManualSetupStatus) ?? "draft",
        trackingEnabled: (setup as any).tracking_enabled ?? true,
        trackingStatus: (setup as any).tracking_status ?? "watching",
        telegramAlertSent: (setup as any).telegram_alert_sent ?? false,
        telegramError: (setup as any).telegram_error ?? null,
        createdAt: setup.created_at ? formatApiDate(setup.created_at) : "--",
        updatedAt: setup.updated_at ? formatApiDate(setup.updated_at) : "--",
      })),
    [setupsQuery.data?.setups]
  )
  const selected = setups.find((setup) => setup.id === selectedId) ?? setups[0] ?? null

  useEffect(() => {
    if (!successMessage) return
    const timer = window.setTimeout(() => setSuccessMessage(null), 2800)
    return () => window.clearTimeout(timer)
  }, [successMessage])

  useEffect(() => {
    if (!selected && setups[0]) {
      setSelectedId(setups[0].id)
    }
  }, [selected, setups])

  function updateField<K extends keyof SetupForm>(key: K, value: SetupForm[K]) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  function resetForm() {
    setForm(DEFAULT_FORM)
    setEditingId(null)
  }

  async function handleSave() {
    if (!preview.canSubmit) return
    const payload = {
      symbol: form.symbol.trim().toUpperCase(),
      direction: form.direction,
      timeframe_pair: form.timeframePair,
      entry_price: Number(form.entryPrice),
      stop_loss: Number(form.stopLoss),
      tp1: Number(form.tp1),
      tp2: Number(form.tp2),
      tp3: Number(form.tp3),
      bias: form.bias,
      confirmation_type: form.confirmationType,
      session: form.session,
      notes: form.notes.trim(),
      activation_mode: form.activationMode,
      move_sl_to_be_after_tp1: form.moveSlToBeAfterTp1,
      enable_telegram_alerts: form.enableTelegramAlerts,
      high_priority: form.highPriority,
      status: inferStatus(form.activationMode),
    }
    try {
      const row = editingId
        ? await updateSetup.mutateAsync({ id: editingId, payload })
        : await createSetup.mutateAsync(payload)
      setSelectedId(row.id)
      setEditingId(row.id)
      if (editingId) {
        setSuccessMessage("Manual setup updated. Tracking active.")
      } else {
        const tgSent = (row as any).telegram_alert_sent
        const tgErr = (row as any).telegram_error
        if (tgSent) {
          setSuccessMessage("Setup saved. Telegram alert sent. Tracking active.")
        } else if (tgErr) {
          setSuccessMessage(`Setup saved. Telegram alert failed — ${tgErr}`)
        } else {
          setSuccessMessage("Setup saved. Tracking active.")
        }
      }
    } catch (err) {
      setSuccessMessage(err instanceof Error ? err.message : "Failed to save manual setup.")
    }
  }

  function handleSelect(id: number) {
    setSelectedId(id)
    const setup = setups.find((item) => item.id === id)
    if (!setup) return
    setEditingId(id)
    setForm({
      symbol: setup.symbol,
      direction: setup.direction,
      timeframePair: setup.timeframePair,
      entryPrice: String(setup.entryPrice),
      stopLoss: String(setup.stopLoss),
      tp1: String(setup.tp1),
      tp2: String(setup.tp2),
      tp3: String(setup.tp3),
      bias: setup.bias,
      confirmationType: setup.confirmationType,
      session: setup.session,
      notes: setup.notes,
      activationMode: setup.activationMode,
      moveSlToBeAfterTp1: setup.moveSlToBeAfterTp1,
      enableTelegramAlerts: setup.enableTelegramAlerts,
      highPriority: setup.highPriority,
    })
  }

  const totals = {
    total: setups.length,
    activeQueue: setups.filter((setup) => ["watching", "pending-order-ready", "activated"].includes(setup.status)).length,
    highPriority: setups.filter((setup) => setup.highPriority).length,
    alertsEnabled: setups.filter((setup) => setup.enableTelegramAlerts).length,
  }

  return (
    <div className="p-4 md:p-6 space-y-5">
      <div className="fixed inset-x-0 top-0 h-44 bg-glow-gold-top pointer-events-none" />

      <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div className="space-y-1">
          <div className="inline-flex items-center gap-2 rounded-full border border-gold-500/20 bg-gold-500/8 px-3 py-1 text-[11px] font-semibold text-gold-400">
            <Sparkles className="h-3.5 w-3.5" />
            Manual Setup Control
          </div>
          <h1 className="text-xl font-bold text-foreground md:text-2xl">Manual Setups</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Build discretionary setups in the FX Unfiltered style, preview them before save, and keep them ready for a later bot-tracking handoff.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <MetricTile label="Total Setups" value={String(totals.total)} accent="gold" icon={Layers3} />
          <MetricTile label="Tracking Queue" value={String(totals.activeQueue)} accent="buy" icon={Eye} />
          <MetricTile label="High Priority" value={String(totals.highPriority)} accent="warn" icon={Flag} />
          <MetricTile label="Alerts Enabled" value={String(totals.alertsEnabled)} accent="gold" icon={Bell} />
        </div>
      </div>

      <AnimatePresence>
        {successMessage ? (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
          >
            <Card glow className={successMessage.toLowerCase().includes("failed") ? "border-sell/30" : "border-buy/30"}>
              <CardContent className="flex items-start gap-3 py-4">
                <div className={`flex h-9 w-9 items-center justify-center rounded-lg border ${successMessage.toLowerCase().includes("failed") ? "border-sell/25 bg-sell/10" : "border-buy/25 bg-buy/10"}`}>
                  <CheckCircle2 className={`h-4 w-4 ${successMessage.toLowerCase().includes("failed") ? "text-sell" : "text-buy"}`} />
                </div>
                <div className="flex-1 space-y-1">
                  <p className="text-sm font-semibold text-foreground">
                    {successMessage.toLowerCase().includes("failed") ? "Telegram alert failed" : "Setup saved successfully"}
                  </p>
                  <p className="text-xs text-muted-foreground">{successMessage}</p>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.2fr)_420px]">
        <div className="space-y-5">
          <ManualSetupForm
            form={form}
            preview={preview}
            onFieldChange={updateField}
            onReset={resetForm}
            onSave={handleSave}
            editing={editingId !== null}
            saving={createSetup.isPending || updateSetup.isPending}
          />
          {setupsQuery.isLoading ? <LoadingCard label="Loading manual setups..." /> : null}
          {setupsQuery.error instanceof Error ? <LoadingCard label={setupsQuery.error.message} error /> : null}
          {!setupsQuery.isLoading && !(setupsQuery.error instanceof Error) ? (
            <ManualSetupList setups={setups} selectedId={selectedId ?? 0} onSelect={handleSelect} />
          ) : null}
        </div>

        <div className="space-y-5">
          <SetupPreviewCard preview={preview} />
          <SetupDetailPanel setup={selected} />
        </div>
      </div>
    </div>
  )
}

function ManualSetupForm({
  form,
  preview,
  onFieldChange,
  onReset,
  onSave,
  editing,
  saving,
}: {
  form: SetupForm
  preview: ReturnType<typeof buildPreview>
  onFieldChange: <K extends keyof SetupForm>(key: K, value: SetupForm[K]) => void
  onReset: () => void
  onSave: () => void
  editing: boolean
  saving: boolean
}) {
  return (
    <Card glow>
      <div className="h-0.5 bg-gradient-to-r from-transparent via-gold-500/55 to-transparent" />
      <CardHeader className="pb-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div>
            <CardTitle>Manual Setup Entry</CardTitle>
            <CardDescription>
              Capture the setup exactly the way the bot will later need to track it.
            </CardDescription>
          </div>
          <Badge variant="gold" className="text-[10px]">
            Premium manual intake
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-5 pt-0">
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="space-y-4 rounded-xl border border-ap-border bg-ap-surface/45 p-4">
            <div className="flex items-center gap-2">
              <ClipboardCheck className="h-4 w-4 text-gold-400" />
              <h3 className="label-xs">Market Context</h3>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Symbol">
                <Input value={form.symbol} onChange={(e) => onFieldChange("symbol", e.target.value)} placeholder="XAUUSD" />
              </Field>
              <Field label="Timeframe Pair">
                <Select value={form.timeframePair} onChange={(e) => onFieldChange("timeframePair", e.target.value as TimeframePair)}>
                  {TIMEFRAME_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Direction">
                <div className="grid grid-cols-2 gap-2">
                  <DirectionButton
                    active={form.direction === "BUY"}
                    label="BUY"
                    onClick={() => onFieldChange("direction", "BUY")}
                  />
                  <DirectionButton
                    active={form.direction === "SELL"}
                    label="SELL"
                    onClick={() => onFieldChange("direction", "SELL")}
                  />
                </div>
              </Field>
              <Field label="Bias">
                <Select value={form.bias} onChange={(e) => onFieldChange("bias", e.target.value as Bias)}>
                  {BIAS_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {formatBias(option)}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Confirmation Type">
                <Select
                  value={form.confirmationType}
                  onChange={(e) => onFieldChange("confirmationType", e.target.value as ConfirmationType)}
                >
                  {CONFIRMATION_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {formatConfirmation(option)}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Session">
                <Select value={form.session} onChange={(e) => onFieldChange("session", e.target.value as Session)}>
                  {SESSION_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {formatSession(option)}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          </div>

          <div className="space-y-4 rounded-xl border border-ap-border bg-ap-surface/45 p-4">
            <div className="flex items-center gap-2">
              <Target className="h-4 w-4 text-gold-400" />
              <h3 className="label-xs">Execution Levels</h3>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Entry Price">
                <Input
                  className="num"
                  value={form.entryPrice}
                  onChange={(e) => onFieldChange("entryPrice", e.target.value)}
                  placeholder="4748.26"
                />
              </Field>
              <Field label="Stop Loss">
                <Input
                  className="num"
                  value={form.stopLoss}
                  onChange={(e) => onFieldChange("stopLoss", e.target.value)}
                  placeholder="4760.80"
                />
              </Field>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="TP1">
                <Input className="num" value={form.tp1} onChange={(e) => onFieldChange("tp1", e.target.value)} placeholder="4732.40" />
              </Field>
              <Field label="TP2">
                <Input className="num" value={form.tp2} onChange={(e) => onFieldChange("tp2", e.target.value)} placeholder="4718.60" />
              </Field>
              <Field label="TP3">
                <Input className="num" value={form.tp3} onChange={(e) => onFieldChange("tp3", e.target.value)} placeholder="4701.25" />
              </Field>
            </div>

            <Field label="Activation Mode">
              <Select
                value={form.activationMode}
                onChange={(e) => onFieldChange("activationMode", e.target.value as ActivationMode)}
              >
                {ACTIVATION_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {formatActivationMode(option)}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        </div>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="space-y-2 rounded-xl border border-ap-border bg-ap-surface/35 p-4">
            <h3 className="label-xs">Notes / Trade Idea</h3>
            <Textarea
              value={form.notes}
              onChange={(e) => onFieldChange("notes", e.target.value)}
              placeholder="Write the reasoning, invalidation, session plan, and why this should enter the bot tracking flow."
            />
          </div>

          <div className="space-y-3 rounded-xl border border-ap-border bg-ap-surface/35 p-4">
            <h3 className="label-xs">Tracking Toggles</h3>
            <ToggleRow
              label="Move SL to BE after TP1"
              description="Prepare this setup for automatic protection after first target."
              checked={form.moveSlToBeAfterTp1}
              onCheckedChange={(checked) => onFieldChange("moveSlToBeAfterTp1", checked)}
            />
            <ToggleRow
              label="Enable Telegram alerts"
              description="Queue alert readiness for manual setup lifecycle events."
              checked={form.enableTelegramAlerts}
              onCheckedChange={(checked) => onFieldChange("enableTelegramAlerts", checked)}
            />
            <ToggleRow
              label="High priority setup"
              description="Push this setup higher in review and monitoring lists."
              checked={form.highPriority}
              onCheckedChange={(checked) => onFieldChange("highPriority", checked)}
            />
          </div>
        </div>
      </CardContent>

      <CardFooter className="flex-col items-stretch gap-3 border-t border-ap-border md:flex-row md:items-center md:justify-between">
        <div className="space-y-1">
          <p className="text-xs font-semibold text-foreground">Readiness check</p>
          <p className="text-xs text-muted-foreground">
            {preview.canSubmit ? "Setup preview is ready to persist into bot tracking." : preview.validationMessage}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onReset}>
            Reset
          </Button>
          <Button onClick={onSave} disabled={!preview.canSubmit || saving}>
            <Plus className="h-4 w-4" />
            {saving ? "Saving..." : editing ? "Update Setup" : "Save Manual Setup"}
          </Button>
        </div>
      </CardFooter>
    </Card>
  )
}

function SetupPreviewCard({ preview }: { preview: ReturnType<typeof buildPreview> }) {
  const directionIsBuy = preview.direction === "BUY"

  return (
    <Card className="sticky top-5">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle>Setup Preview</CardTitle>
            <CardDescription>Review the payload before it enters tracking.</CardDescription>
          </div>
          <Badge variant={preview.highPriority ? "warn" : "muted"} className="text-[10px]">
            {preview.highPriority ? "High Priority" : "Standard Priority"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        <div className="rounded-xl border border-ap-border bg-ap-surface/45 p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="space-y-1">
              <div className="text-[11px] font-medium text-muted-foreground">{preview.timeframePair}</div>
              <div className="flex items-center gap-2">
                <span className="num text-xl font-bold text-foreground">{preview.entryLabel}</span>
                <Badge variant="gold" className="text-[10px]">
                  {preview.symbol}
                </Badge>
              </div>
            </div>
            <div
              className={cn(
                "inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-semibold border",
                directionIsBuy ? "bg-buy/10 border-buy/25 text-buy" : "bg-sell/10 border-sell/25 text-sell"
              )}
            >
              {directionIsBuy ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
              {preview.direction}
            </div>
          </div>
          <div className="mt-3 grid grid-cols-4 gap-2">
            {preview.levels.map((level) => (
              <div key={level.label} className="rounded-lg border border-ap-border bg-ap-card px-2.5 py-2 text-center">
                <div className="label-xs text-[9px]">{level.label}</div>
                <div className={cn("num mt-1 text-[11px] font-semibold", level.color)}>{level.value}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <MetaBlock label="Bias" value={formatBias(preview.bias)} />
          <MetaBlock label="Confirmation" value={formatConfirmation(preview.confirmationType)} />
          <MetaBlock label="Session" value={formatSession(preview.session)} />
          <MetaBlock label="Activation" value={formatActivationMode(preview.activationMode)} />
        </div>

        <div className="rounded-xl border border-ap-border bg-ap-surface/35 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="label-xs">Projected Structure</span>
            <span className="num text-sm font-semibold text-gold-400">{preview.rrLabel}</span>
          </div>
          <div className="space-y-2">
            <ProgressRow label="Risk to SL" value={preview.riskLabel} tone="sell" />
            <ProgressRow label="TP1 Potential" value={preview.tp1DistanceLabel} tone="buy" />
            <ProgressRow label="Tracking Readiness" value={preview.canSubmit ? "Ready" : "Incomplete"} tone={preview.canSubmit ? "gold" : "warn"} />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function ManualSetupList({
  setups,
  selectedId,
  onSelect,
}: {
  setups: ManualSetup[]
  selectedId: number
  onSelect: (id: number) => void
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <CardTitle>Manual Setup History</CardTitle>
            <CardDescription>Local mock list for handoff-ready setups and status tracking.</CardDescription>
          </div>
          <Badge variant="muted" className="text-[10px]">
            {setups.length} saved setups
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        {setups.map((setup) => {
          const isActive = selectedId === setup.id
          const statusCfg = STATUS_CFG[setup.status]

          return (
            <button
              key={setup.id}
              type="button"
              onClick={() => onSelect(setup.id)}
              className={cn(
                "w-full rounded-xl border p-4 text-left transition-all duration-200",
                "bg-ap-surface/35 hover:bg-ap-surface/55",
                isActive ? "border-gold-500/40 shadow-gold-xs" : statusCfg.glow
              )}
            >
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="num text-sm font-bold text-foreground">{setup.symbol}</span>
                    <Badge variant={setup.direction === "BUY" ? "buy" : "sell"} className="text-[10px]">
                      {setup.direction}
                    </Badge>
                    <Badge variant={statusCfg.variant} className="text-[10px]">
                      {setup.status}
                    </Badge>
                    {setup.highPriority ? (
                      <Badge variant="warn" className="text-[10px]">
                        high priority
                      </Badge>
                    ) : null}
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    {setup.timeframePair} | {formatConfirmation(setup.confirmationType)} | {formatActivationMode(setup.activationMode)}
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-[11px] text-muted-foreground sm:grid-cols-4">
                    <MiniStat label="Entry" value={formatPrice(setup.entryPrice)} />
                    <MiniStat label="TP1" value={formatPrice(setup.tp1)} />
                    <MiniStat label="Session" value={formatSession(setup.session)} />
                    <MiniStat label="Bias" value={compactBias(setup.bias)} />
                  </div>
                </div>

                <div className="space-y-2 text-right">
                  <div className="text-[10px] text-muted-foreground">Updated {setup.updatedAt}</div>
                  <div className="num text-xs font-semibold text-gold-400">{computeRr(setup.entryPrice, setup.stopLoss, setup.tp1)}</div>
                  <div className="text-[10px] text-muted-foreground">
                    {setup.enableTelegramAlerts ? "Telegram on" : "Telegram off"} | {setup.moveSlToBeAfterTp1 ? "BE after TP1" : "Manual SL"}
                  </div>
                </div>
              </div>
            </button>
          )
        })}
      </CardContent>
    </Card>
  )
}

function SetupDetailPanel({ setup }: { setup: ManualSetup | null }) {
  if (!setup) {
    return (
      <Card>
        <CardContent className="py-16 text-center">
          <p className="text-sm font-semibold text-foreground">No setup selected</p>
          <p className="mt-1 text-xs text-muted-foreground">Choose a manual setup to inspect its tracking payload.</p>
        </CardContent>
      </Card>
    )
  }

  const statusIndex = STATUS_ORDER.indexOf(setup.status)
  const directionIsBuy = setup.direction === "BUY"

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle>Setup Detail Panel</CardTitle>
            <CardDescription>How this setup will look before future bot-tracking integration.</CardDescription>
          </div>
          <Badge variant={STATUS_CFG[setup.status].variant} className="text-[10px]">
            {setup.status}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        <div className="rounded-xl border border-ap-border bg-ap-surface/45 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="num text-lg font-bold text-foreground">{setup.symbol}</div>
              <div className="mt-1 text-[11px] text-muted-foreground">{setup.timeframePair}</div>
            </div>
            <div className={cn("inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-semibold border",
              directionIsBuy ? "bg-buy/10 border-buy/25 text-buy" : "bg-sell/10 border-sell/25 text-sell")}>
              {directionIsBuy ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
              {setup.direction}
            </div>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-2">
            <MetaBlock label="Bias" value={formatBias(setup.bias)} />
            <MetaBlock label="Confirmation" value={formatConfirmation(setup.confirmationType)} />
            <MetaBlock label="Session" value={formatSession(setup.session)} />
            <MetaBlock label="Activation Mode" value={formatActivationMode(setup.activationMode)} />
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-ap-border bg-ap-surface/35 p-4">
          <div className="flex items-center gap-2">
            <Radio className="h-4 w-4 text-gold-400" />
            <h3 className="label-xs">Status Journey</h3>
          </div>
          <div className="space-y-2">
            {STATUS_ORDER.map((status, index) => {
              const complete = index <= statusIndex
              return (
                <div key={status} className="flex items-center gap-3">
                  <div
                    className={cn(
                      "flex h-6 w-6 items-center justify-center rounded-full border text-[10px] font-semibold",
                      complete ? "border-gold-500/30 bg-gold-500/15 text-gold-400" : "border-ap-border bg-ap-surface text-muted-foreground"
                    )}
                  >
                    {index + 1}
                  </div>
                  <span className={cn("text-xs", complete ? "text-foreground" : "text-muted-foreground")}>{status}</span>
                </div>
              )
            })}
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-ap-border bg-ap-surface/35 p-4">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-gold-400" />
            <h3 className="label-xs">Tracking Flags</h3>
          </div>
          <div className="grid grid-cols-1 gap-2">
            <FlagPill active={setup.moveSlToBeAfterTp1} label="Move SL to BE after TP1" />
            <FlagPill active={setup.enableTelegramAlerts} label="Telegram alerts enabled" />
            <FlagPill active={setup.highPriority} label="High priority review lane" />
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-ap-border bg-ap-surface/35 p-4">
          <div className="flex items-center gap-2">
            <Clock3 className="h-4 w-4 text-gold-400" />
            <h3 className="label-xs">Levels + Notes</h3>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <LevelChip label="Entry" value={setup.entryPrice} tone="foreground" />
            <LevelChip label="SL" value={setup.stopLoss} tone="sell" />
            <LevelChip label="TP1" value={setup.tp1} tone="buy" />
            <LevelChip label="TP2 / TP3" value={`${formatPrice(setup.tp2)} / ${formatPrice(setup.tp3)}`} tone="gold" />
          </div>
          <Separator />
          <p className="text-xs leading-6 text-muted-foreground">{setup.notes || "No notes added yet."}</p>
        </div>
      </CardContent>
    </Card>
  )
}

function MetricTile({
  label,
  value,
  accent,
  icon: Icon,
}: {
  label: string
  value: string
  accent: "gold" | "buy" | "warn"
  icon: ComponentType<{ className?: string }>
}) {
  const tone =
    accent === "buy"
      ? "border-buy/20 bg-buy/8 text-buy"
      : accent === "warn"
      ? "border-warn/20 bg-warn/10 text-warn"
      : "border-gold-500/20 bg-gold-500/8 text-gold-400"

  return (
    <Card className="overflow-hidden">
      <CardContent className="flex items-center gap-3 py-4">
        <div className={cn("flex h-10 w-10 items-center justify-center rounded-lg border", tone)}>
          <Icon className="h-4 w-4" />
        </div>
        <div>
          <div className="label-xs">{label}</div>
          <div className="num text-lg font-bold text-foreground">{value}</div>
        </div>
      </CardContent>
    </Card>
  )
}

function DirectionButton({ active, label, onClick }: { active: boolean; label: Direction; onClick: () => void }) {
  const isBuy = label === "BUY"

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center justify-center gap-1 rounded-lg border px-3 py-2 text-xs font-semibold transition-all",
        active
          ? isBuy
            ? "border-buy/25 bg-buy/10 text-buy"
            : "border-sell/25 bg-sell/10 text-sell"
          : "border-ap-border bg-ap-card text-muted-foreground hover:text-foreground hover:border-ap-border-strong"
      )}
    >
      {isBuy ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
      {label}
    </button>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="label-xs">{label}</label>
      {children}
    </div>
  )
}

function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cn(
        "flex h-9 w-full rounded-md border border-ap-border bg-ap-surface px-3 py-1 text-sm text-foreground",
        "focus:outline-none focus:ring-2 focus:ring-ring focus:border-gold-600",
        props.className
      )}
    />
  )
}

function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cn(
        "min-h-[138px] w-full rounded-md border border-ap-border bg-ap-surface px-3 py-2 text-sm text-foreground",
        "placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-gold-600",
        props.className
      )}
    />
  )
}

function ToggleRow({
  label,
  description,
  checked,
  onCheckedChange,
}: {
  label: string
  description: string
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-ap-border bg-ap-card px-3 py-3">
      <div className="space-y-1">
        <p className="text-xs font-semibold text-foreground">{label}</p>
        <p className="text-[11px] leading-5 text-muted-foreground">{description}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  )
}

function MetaBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-ap-border bg-ap-card px-3 py-2.5">
      <div className="label-xs">{label}</div>
      <div className="mt-1 text-xs font-semibold text-foreground">{value}</div>
    </div>
  )
}

function ProgressRow({ label, value, tone }: { label: string; value: string; tone: "buy" | "sell" | "gold" | "warn" }) {
  const color =
    tone === "buy" ? "text-buy" : tone === "sell" ? "text-sell" : tone === "warn" ? "text-warn" : "text-gold-400"
  return (
    <div className="flex items-center justify-between rounded-lg border border-ap-border bg-ap-card px-3 py-2">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className={cn("num text-xs font-semibold", color)}>{value}</span>
    </div>
  )
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-ap-border bg-ap-card px-2 py-1.5">
      <div className="label-xs text-[9px]">{label}</div>
      <div className="mt-0.5 text-[11px] font-medium text-foreground">{value}</div>
    </div>
  )
}

function FlagPill({ active, label }: { active: boolean; label: string }) {
  return (
    <div
      className={cn(
        "flex items-center justify-between rounded-lg border px-3 py-2 text-xs",
        active ? "border-gold-500/25 bg-gold-500/10 text-foreground" : "border-ap-border bg-ap-card text-muted-foreground"
      )}
    >
      <span>{label}</span>
      <span className={cn("font-semibold", active ? "text-gold-400" : "text-muted-foreground")}>{active ? "On" : "Off"}</span>
    </div>
  )
}

function LevelChip({ label, value, tone }: { label: string; value: number | string; tone: "sell" | "buy" | "gold" | "foreground" }) {
  const color =
    tone === "sell" ? "text-sell" : tone === "buy" ? "text-buy" : tone === "gold" ? "text-gold-400" : "text-foreground"

  return (
    <div className="rounded-lg border border-ap-border bg-ap-card px-3 py-2">
      <div className="label-xs text-[9px]">{label}</div>
      <div className={cn("num mt-1 text-xs font-semibold", color)}>
        {typeof value === "number" ? formatPrice(value) : value}
      </div>
    </div>
  )
}

function buildPreview(form: SetupForm) {
  const entry = Number(form.entryPrice)
  const stop = Number(form.stopLoss)
  const tp1 = Number(form.tp1)
  const tp2 = Number(form.tp2)
  const tp3 = Number(form.tp3)
  const requiredReady = form.symbol.trim() && Number.isFinite(entry) && Number.isFinite(stop) && Number.isFinite(tp1)

  let validationMessage = "Fill symbol, entry, stop loss, and TP1 to save this setup."

  if (requiredReady) {
    if (entry === stop) {
      validationMessage = "Entry and stop loss cannot be equal."
    } else if ((form.direction === "BUY" && !(tp1 > entry)) || (form.direction === "SELL" && !(tp1 < entry))) {
      validationMessage = "TP1 should sit in the trade direction."
    } else {
      validationMessage = "Ready to save."
    }
  }

  const canSubmit = validationMessage === "Ready to save."

  return {
    symbol: form.symbol.trim().toUpperCase() || "SYMBOL",
    direction: form.direction,
    timeframePair: form.timeframePair,
    bias: form.bias,
    confirmationType: form.confirmationType,
    session: form.session,
    activationMode: form.activationMode,
    highPriority: form.highPriority,
    entryLabel: Number.isFinite(entry) ? formatPrice(entry) : "--",
    rrLabel:
      Number.isFinite(entry) && Number.isFinite(stop) && Number.isFinite(tp1)
        ? computeRr(entry, stop, tp1)
        : "--",
    riskLabel:
      Number.isFinite(entry) && Number.isFinite(stop)
        ? formatDistance(Math.abs(entry - stop))
        : "--",
    tp1DistanceLabel:
      Number.isFinite(entry) && Number.isFinite(tp1)
        ? formatDistance(Math.abs(tp1 - entry))
        : "--",
    canSubmit,
    validationMessage,
    levels: [
      { label: "Entry", value: Number.isFinite(entry) ? formatPrice(entry) : "--", color: "text-foreground" },
      { label: "SL", value: Number.isFinite(stop) ? formatPrice(stop) : "--", color: "text-sell" },
      { label: "TP1", value: Number.isFinite(tp1) ? formatPrice(tp1) : "--", color: "text-buy" },
      { label: "TP2 / TP3", value: `${Number.isFinite(tp2) ? formatPrice(tp2) : "--"} / ${Number.isFinite(tp3) ? formatPrice(tp3) : "--"}`, color: "text-gold-400" },
    ],
  }
}

function inferStatus(mode: ActivationMode): ManualSetupStatus {
  if (mode === "pending_order_ready") return "pending-order-ready"
  if (mode === "manual_activation") return "draft"
  return "watching"
}

function formatPrice(value: number) {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatDistance(value: number) {
  return `${value.toFixed(1)} pts`
}

function computeRr(entry: number, stop: number, target: number) {
  const risk = Math.abs(entry - stop)
  const reward = Math.abs(target - entry)
  if (risk === 0) return "--"
  return `1:${(reward / risk).toFixed(1)}`
}

function formatBias(value: Bias) {
  return value
    .split(" ")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

function compactBias(value: Bias) {
  const [side, strength] = value.split(" ")
  return `${side.charAt(0).toUpperCase()} ${strength ? strength.charAt(0).toUpperCase() : ""}`.trim()
}

function formatConfirmation(value: ConfirmationType) {
  return value.replace(/_/g, " ")
}

function formatSession(value: Session) {
  return value === "new_york" ? "New York" : value === "off_session" ? "Off Session" : value.charAt(0).toUpperCase() + value.slice(1)
}

function formatActivationMode(value: ActivationMode) {
  return value.replace(/_/g, " ")
}

function formatApiDate(value: string) {
  const dt = new Date(value)
  if (Number.isNaN(dt.getTime())) return value
  return dt.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function LoadingCard({ label, error = false }: { label: string; error?: boolean }) {
  return (
    <Card>
      <CardContent className="py-8 text-center">
        <p className={cn("text-sm", error ? "text-sell" : "text-muted-foreground")}>{label}</p>
      </CardContent>
    </Card>
  )
}
