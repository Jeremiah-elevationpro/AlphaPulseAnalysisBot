import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart2,
  Bell,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  Globe2,
  Layers,
  ShieldCheck,
  Target,
  Wrench,
} from "lucide-react"
import { useMemo, useState } from "react"
import { Link } from "react-router-dom"

import { BotControlPanel } from "@/components/control/BotControlPanel"
import { ReplayRunner } from "@/components/control/ReplayRunner"
import { SpencerStatus } from "@/components/control/SpencerStatus"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import {
  useActiveTrades,
  useAlerts,
  useAnalytics,
  useBotStatus,
  useDataHealth,
  useHealth,
  useLearningProfiles,
  useMarketContext,
  useReplayLatest,
  useSignals,
  useTelegramHealth,
} from "@/hooks/use-data"
import type {
  AIPredictiveLayerState,
  ConfirmationEngineState,
  DecisionEngineState,
  DeveloperDiagnosticsState,
  LearningScoringState,
  LevelIntelligenceState,
  MarketAnalystState,
  RiskTradeManagementState,
  SessionLiquidityState,
  SpencerStatus as SpencerStatusBlock,
  SystemHealthState,
  TelegramHealthResponse,
} from "@/lib/api"
import { cn } from "@/lib/utils"

export default function Dashboard() {
  const health = useHealth()
  const dataHealth = useDataHealth()
  const analytics = useAnalytics()
  const signals = useSignals(6)
  const trades = useActiveTrades(6)
  const alerts = useAlerts(6)
  const botStatus = useBotStatus()
  const learningProfiles = useLearningProfiles()
  const replayLatest = useReplayLatest()
  const telegramHealth = useTelegramHealth(false)
  const isBotOnline = ["online", "analyzing", "watching", "starting"].includes(botStatus.data?.status ?? "")
  const market = useMarketContext(botStatus.data?.symbol ?? "XAUUSD", isBotOnline)

  const data = botStatus.data?.data ?? null
  const spencer: SpencerStatusBlock = data?.spencerStatus ?? {}
  const marketAnalyst: MarketAnalystState = data?.marketAnalyst ?? {}
  const confirmationEngine: ConfirmationEngineState = data?.confirmationEngine ?? {}
  const learningScoring: LearningScoringState = data?.learningScoring ?? {}
  const aiLayer: AIPredictiveLayerState = (data?.aiPredictiveLayer ?? {}) as AIPredictiveLayerState
  const decisionEngine: DecisionEngineState = data?.decisionEngine ?? {}
  const levelIntel: LevelIntelligenceState = data?.levelIntelligence ?? {}
  const riskTradeMgmt: RiskTradeManagementState = data?.riskTradeManagement ?? {}
  const systemHealth: SystemHealthState = data?.systemHealth ?? {}
  const developerDiag: DeveloperDiagnosticsState = data?.developerDiagnostics ?? {}
  const priceFeed = data?.priceFeed ?? {}

  const learningMap = useMemo(
    () => Object.fromEntries((learningProfiles.data?.profiles ?? []).map((profile) => [profile.strategy_type, profile])),
    [learningProfiles.data?.profiles]
  )
  const replaySummary = replayLatest.data?.result ?? null

  return (
    <div className="space-y-5 p-4 md:p-6">
      <div className="pointer-events-none fixed inset-x-0 top-0 h-44 bg-glow-purple-top" />
      <div className="pointer-events-none fixed inset-x-0 top-0 h-28 bg-glow-gold-top" />

      {/* A. Top Status Bar */}
      <Card glow>
        <CardContent className="relative overflow-hidden py-5">
          <div className="pointer-events-none absolute inset-y-0 right-0 w-1/3 bg-gradient-to-l from-purple-500/14 via-gold-500/8 to-transparent" />
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="space-y-2">
              <div className="inline-flex items-center gap-2 rounded-full border border-gold-500/20 bg-gold-500/8 px-3 py-1 text-[11px] font-semibold text-gold-300">
                <Activity className="h-3.5 w-3.5" />
                Spencer v1.1.0 — Live Engine
              </div>
              <div>
                <h2 className="text-xl font-bold text-foreground md:text-2xl">Powered by AlphaPulse</h2>
                <p className="max-w-2xl text-sm text-muted-foreground">
                  Market Analyst → Confirmation → Learning → AI Advisory → Decision → Risk Management.
                </p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={spencer.running ? "buy" : "sell"} className="text-[10px]">
                {spencer.running ? "Running" : "Stopped"}
              </Badge>
              <Badge variant="gold" className="text-[10px]">
                {(spencer.symbol ?? botStatus.data?.symbol ?? "XAUUSD").toUpperCase()}
              </Badge>
              <Badge variant="purple" className="text-[10px]">
                {spencer.currentPrice != null ? `Price ${Number(spencer.currentPrice).toFixed(2)}` : "Price --"}
              </Badge>
              <Badge variant="outline" className="text-[10px]">{formatSession(spencer.currentSession)}</Badge>
              <Badge variant={systemHealth.dataFeedStatus === "healthy" ? "buy" : "sell"} className="text-[10px]">
                Data feed: {systemHealth.dataFeedStatus ?? "unknown"}
              </Badge>
              <Badge variant="outline" className="text-[10px]">
                Last scan: {formatRelative(spencer.lastScanAt)}
              </Badge>
              <Button asChild size="sm">
                <Link to="/signals">Review Signals</Link>
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link to="/settings">Open Controls</Link>
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <SpencerStatus status={botStatus.data} />

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-gold-500/20 bg-gold-500/8 text-gold-300">
              <BarChart2 className="h-4 w-4" />
            </div>
            <CardTitle>Spencer Live Price Action</CardTitle>
            <Badge variant="outline" className="ml-auto text-[10px]">
              {priceFeed.timeframe ?? "M15"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 md:grid-cols-4">
          <InfoRow label="Current Price" value={priceFeed.currentPrice != null ? Number(priceFeed.currentPrice).toFixed(2) : "Waiting"} mono />
          <InfoRow label="Active Level" value={priceFeed.activeLevel != null ? Number(priceFeed.activeLevel).toFixed(2) : "—"} mono />
          <InfoRow label="Primary Zone" value={priceFeed.primaryZone ?? "—"} mono />
          <InfoRow label="Alternative Zone" value={priceFeed.alternativeZone ?? "—"} mono />
          <InfoRow label="SL" value={priceFeed.sl != null ? Number(priceFeed.sl).toFixed(2) : "—"} mono />
          <InfoRow label="TP1 / TP2 / TP3" value={[priceFeed.tp1, priceFeed.tp2, priceFeed.tp3].filter((v) => v != null).map((v) => Number(v).toFixed(2)).join(" / ") || "—"} mono />
          <InfoRow label="Candles" value={(priceFeed.latestCandles?.length ?? 0) > 0 ? `${priceFeed.latestCandles?.length} loaded` : "chart integration pending"} />
          <InfoRow label="Updated" value={formatRelative(priceFeed.lastUpdated)} />
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <BotControlPanel />
        <ReplayRunner />
      </div>

      {/* B. Market Analyst Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-gold-500/20 bg-gold-500/8 text-gold-300">
              <Globe2 className="h-4 w-4" />
            </div>
            <CardTitle>Market Analyst</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
            <div className="text-xs font-semibold text-gold-300">Bias & Context</div>
            <div className={cn("text-sm font-semibold", biasColor(marketAnalyst.dominantBias ?? market.data?.bias?.dominant))}>
              {formatBiasLabel(marketAnalyst.dominantBias ?? market.data?.bias?.dominant)}
              {marketAnalyst.biasStrength ? ` (${marketAnalyst.biasStrength})` : ""}
            </div>
            <Separator />
            <InfoRow label="H4 Context" value={marketAnalyst.h4Context ?? "Waiting for plan"} />
            <InfoRow label="H1 Context" value={marketAnalyst.h1Context ?? "—"} />
            <InfoRow label="M15 Context" value={marketAnalyst.m15Context ?? "—"} />
            <InfoRow label="Market Condition" value={marketAnalyst.marketCondition ?? "—"} />
            <InfoRow label="Scenario Status" value={marketAnalyst.scenarioStatus ?? "—"} />
          </div>
          <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
            <div className="text-xs font-semibold text-gold-300">Scenario Watchlist</div>
            <InfoRow
              label="Primary"
              value={
                marketAnalyst.primaryScenario
                  ? `${marketAnalyst.primaryScenario.direction ?? "?"} ${marketAnalyst.primaryScenario.watch_zone ?? ""}`
                  : "Waiting for primary scenario"
              }
            />
            <InfoRow
              label="Secondary"
              value={
                marketAnalyst.secondaryScenario
                  ? `${marketAnalyst.secondaryScenario.direction ?? "?"} ${marketAnalyst.secondaryScenario.watch_zone ?? ""}`
                  : "Waiting for secondary scenario"
              }
            />
            <InfoRow
              label="Watch Zones"
              value={
                (marketAnalyst.watchZones ?? [])
                  .slice(0, 3)
                  .map((z) => `${z.direction} ${Number(z.level_low ?? 0).toFixed(2)}-${Number(z.level_high ?? 0).toFixed(2)} (${z.status})`)
                  .join(" | ") || "No active watch zones"
              }
            />
            <InfoRow
              label="Psychological Levels"
              value={(marketAnalyst.psychologicalLevels ?? []).slice(0, 12).map((v) => v.toFixed(2)).join(" | ") || "—"}
            />
            <InfoRow
              label="Key Supports"
              value={(marketAnalyst.keySupports ?? []).slice(0, 4).map((v) => v.toFixed(2)).join(" | ") || "—"}
            />
            <InfoRow
              label="Key Resistances"
              value={(marketAnalyst.keyResistances ?? []).slice(0, 4).map((v) => v.toFixed(2)).join(" | ") || "—"}
            />
          </div>
        </CardContent>
      </Card>

      {/* D. Level Intelligence Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-gold-500/20 bg-gold-500/8 text-gold-300">
              <Layers className="h-4 w-4" />
            </div>
            <CardTitle>Level Intelligence</CardTitle>
            <Badge variant={levelIntel.blocking_mode ? "sell" : "outline"} className="ml-auto text-[10px]">
              {levelIntel.blocking_mode ? "Blocking" : "Advisory only"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
            <InfoRow label="Strongest Support" value={formatLevelIntel(levelIntel.strongest_support)} />
            <InfoRow label="Strongest Resistance" value={formatLevelIntel(levelIntel.strongest_resistance)} />
            <InfoRow label="Next Valid Targets" value={(levelIntel.next_valid_targets ?? []).slice(0, 5).map(formatLevelIntel).join(" | ") || "-"} />
          </div>
          <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
            <InfoRow label="Consumed Levels" value={(levelIntel.consumed_levels ?? []).slice(0, 5).map(formatLevelIntel).join(" | ") || "none"} />
            <InfoRow label="Manual Levels Watched" value={String(levelIntel.manual_levels_watched ?? 0)} />
            <InfoRow label="Top Evidence" value={(levelIntel.level_scores ?? [])[0]?.evidence_summary ?? "-"} />
          </div>
        </CardContent>
      </Card>

      {/* D. Entry Decision Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-purple-400/25 bg-purple-500/12 text-purple-200">
              <Target className="h-4 w-4" />
            </div>
            <CardTitle>Entry Decision</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
            <div className="text-xs font-semibold text-gold-300">Confirmation Engine</div>
            <InfoRow label="Last Confirmation" value={confirmationEngine.lastConfirmation ?? confirmationEngine.confirmationType ?? "Waiting"} />
            <InfoRow label="Type" value={confirmationEngine.confirmationType ?? "—"} />
            <InfoRow label="Grade" value={confirmationEngine.confirmationGrade ?? "—"} />
            <InfoRow label="Score" value={confirmationEngine.confirmationScore != null ? String(confirmationEngine.confirmationScore) : "—"} mono />
            <InfoRow label="Status" value={confirmationEngine.confirmationStatus ?? "waiting"} />
            <InfoRow label="Watching For" value={(confirmationEngine.watchingFor ?? []).slice(0, 5).join(" / ") || "—"} />
            <InfoRow label="Stale Blocked" value={String(Boolean(confirmationEngine.staleConfirmationBlocked))} />
            <InfoRow label="TP1 Already Reached" value={String(Boolean(confirmationEngine.tp1AlreadyReachedBlocked))} />
            <InfoRow label="Chase Blocked" value={String(Boolean(confirmationEngine.chaseBlocked))} />
          </div>
          <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
            <div className="text-xs font-semibold text-gold-300">Decision Engine</div>
            <InfoRow label="Final Decision" value={decisionEngine.finalDecision ?? "wait"} />
            <InfoRow label="Reason" value={decisionEngine.decisionReason ?? "—"} />
            <InfoRow
              label="Candidate Rank Score"
              value={decisionEngine.candidateRankScore != null ? Number(decisionEngine.candidateRankScore).toFixed(1) : "—"}
              mono
            />
            <InfoRow label="Setup Quality" value={decisionEngine.setupQualityLabel ?? "—"} />
            <InfoRow label="Blocked Reason" value={decisionEngine.blockedReason ?? "—"} />
            <InfoRow label="Alert Allowed" value={String(decisionEngine.alertAllowed ?? true)} />
            <InfoRow label="Scenario Compliance" value={decisionEngine.scenarioComplianceStatus ?? "pass"} />
            <InfoRow label="Compliance Reason" value={decisionEngine.scenarioComplianceReason ?? "-"} />
            <InfoRow label="Corrected Status" value={decisionEngine.correctedStatus ?? "actionable"} />
            <InfoRow label="Advisory Notes" value={decisionEngine.advisoryNotes ?? "—"} />

            <Separator />
            <div className="text-xs font-semibold text-gold-300">Learning / Scoring</div>
            <InfoRow label="Learning Action" value={learningScoring.learningAction ?? "allow"} />
            <InfoRow label="Score" value={learningScoring.learningScore != null ? Number(learningScoring.learningScore).toFixed(1) : "—"} mono />
            <InfoRow label="Profile" value={learningScoring.profileUsed ?? "—"} />
            <InfoRow label="Sample Size" value={learningScoring.sampleSize != null ? String(learningScoring.sampleSize) : "—"} mono />
            <InfoRow label="Confidence Tier" value={learningScoring.confidenceTier ?? "—"} />
            <InfoRow label="Historical Win Rate" value={formatPercent(learningScoring.historicalWinRate)} />
          </div>
        </CardContent>
      </Card>

      {/* E. AI Predictive Layer Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-purple-400/25 bg-purple-500/12 text-purple-200">
              <BrainCircuit className="h-4 w-4" />
            </div>
            <CardTitle>AI Predictive Layer</CardTitle>
            <Badge variant={aiLayer.blockingMode ? "sell" : "outline"} className="ml-auto text-[10px]">
              {aiLayer.blockingMode ? "Blocking" : "Advisory only"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
          <MetaBox label="Enabled" value={String(Boolean(aiLayer.enabled))} tone="gold" />
          <MetaBox label="Model Type" value={aiLayer.modelType ?? "approved_setup_outcome"} tone="gold" />
          <MetaBox label="Model Version" value={aiLayer.modelVersion ?? "—"} tone="gold" />
          <MetaBox label="AI Label" value={aiLayer.aiLabel ?? "AI-ALLOWED SETUP"} tone="gold" />
          <MetaBox label="Recommendation" value={aiLayer.recommendation ?? "allow"} tone="gold" />
          <MetaBox label="TP1 Probability" value={formatPercent(aiLayer.tp1Probability)} tone="buy" />
          <MetaBox label="SL Probability" value={formatPercent(aiLayer.slProbability)} tone="sell" />
          <MetaBox label="Expected Pips" value={formatSignedNumber(aiLayer.expectedPips)} tone="gold" />
          <MetaBox label="Confidence" value={formatPercent(aiLayer.modelConfidence)} tone="gold" />
          <MetaBox
            label="Feature Health"
            value={`Missing ${aiLayer.missingFeaturesCount ?? 0} / Unknown ${aiLayer.unknownCategoriesCount ?? 0}`}
            tone={aiLayer.missingFeaturesCount || aiLayer.unknownCategoriesCount ? "sell" : "buy"}
          />
          <MetaBox label="Schema Match" value={String(aiLayer.schemaMatch ?? true)} tone={aiLayer.schemaMatch === false ? "sell" : "gold"} />
          <MetaBox label="Would Block" value={String(Boolean(aiLayer.wouldBlock))} tone={aiLayer.wouldBlock ? "sell" : "gold"} />
          <MetaBox label="Last Prediction" value={formatRelative(aiLayer.lastPredictionAt)} tone="gold" />
        </CardContent>
      </Card>

      {/* F. Risk / Trade Management Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-buy/20 bg-buy/10 text-buy">
              <ShieldCheck className="h-4 w-4" />
            </div>
            <CardTitle>Risk / Trade Management</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-6">
          <MetaBox label="Active Trade" value={riskTradeMgmt.activeTrade ? "yes" : "none"} tone={riskTradeMgmt.activeTrade ? "buy" : "gold"} />
          <MetaBox label="Direction" value={riskTradeMgmt.direction ?? "n/a"} tone="gold" />
          <MetaBox label="Status" value={riskTradeMgmt.tradeStatus ?? "idle"} tone="gold" />
          <MetaBox label="Entry" value={formatMaybe(riskTradeMgmt.entry)} tone="gold" />
          <MetaBox label="SL" value={formatMaybe(riskTradeMgmt.sl)} tone="sell" />
          <MetaBox label="TP1" value={formatMaybe(riskTradeMgmt.tp1)} tone="buy" />
          <MetaBox label="TP2" value={formatMaybe(riskTradeMgmt.tp2)} tone="buy" />
          <MetaBox label="TP3" value={formatMaybe(riskTradeMgmt.tp3)} tone="buy" />
          <MetaBox label="Risk pips" value={formatMaybe(riskTradeMgmt.riskPips, 0)} tone="gold" />
          <MetaBox label="TP1 RR" value={riskTradeMgmt.tp1RR != null ? `${Number(riskTradeMgmt.tp1RR).toFixed(2)}R` : "—"} tone="gold" />
          <MetaBox label="BE Status" value={riskTradeMgmt.moveToBEStatus ?? "—"} tone="gold" />
          <MetaBox label="TP1 Hit" value={riskTradeMgmt.tp1Hit ? "yes" : "no"} tone={riskTradeMgmt.tp1Hit ? "buy" : "gold"} />
          <MetaBox label="TP2 Hit" value={riskTradeMgmt.tp2Hit ? "yes" : "no"} tone={riskTradeMgmt.tp2Hit ? "buy" : "gold"} />
          <MetaBox label="TP3 Hit" value={riskTradeMgmt.tp3Hit ? "yes" : "no"} tone={riskTradeMgmt.tp3Hit ? "buy" : "gold"} />
          <MetaBox label="Protected after TP1" value={String(Boolean(riskTradeMgmt.protectedAfterTp1))} tone="gold" />
          <MetaBox label="Last TM Alert" value={riskTradeMgmt.lastTradeManagementAlert ?? "â€”"} tone="gold" />
          <MetaBox label="Active Trades" value={String(riskTradeMgmt.activeTradesCount ?? 0)} tone="gold" />
          <MetaBox label="Merged Duplicates" value={String(riskTradeMgmt.duplicateTradesMergedCount ?? 0)} tone="gold" />
          <MetaBox label="Recovered Muted" value={String(riskTradeMgmt.recoveredTradesMutedCount ?? 0)} tone="gold" />
          <MetaBox label="Lifecycle Enabled" value={String(riskTradeMgmt.lifecycleAlertsEnabledCount ?? 0)} tone="buy" />
          <MetaBox label="Tracking Started" value={riskTradeMgmt.tradeTrackingStartedAt ? new Date(riskTradeMgmt.tradeTrackingStartedAt).toLocaleTimeString() : "-"} tone="gold" />
          <MetaBox label="Last TP Alert" value={riskTradeMgmt.lastTpAlertSent ? new Date(riskTradeMgmt.lastTpAlertSent).toLocaleTimeString() : "-"} tone="gold" />
          <MetaBox
            label="Invalidation"
            value={riskTradeMgmt.invalidationLevel != null ? String(riskTradeMgmt.invalidationLevel) : "—"}
            tone="gold"
          />
        </CardContent>
      </Card>

      {/* G. Learning / Replay Snapshot */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-gold-500/20 bg-gold-500/8 text-gold-300">
              <BarChart2 className="h-4 w-4" />
            </div>
            <CardTitle>Learning / Replay Snapshot</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-4">
          <MetaBox label="Last Replay" value={replayLatest.data?.timestamp ? new Date(replayLatest.data.timestamp).toLocaleString() : "—"} tone="gold" />
          <MetaBox label="Net Pips" value={replaySummary?.netPips != null ? `${replaySummary.netPips >= 0 ? "+" : ""}${replaySummary.netPips}` : "—"} tone={replaySummary && replaySummary.netPips >= 0 ? "buy" : "sell"} />
          <MetaBox label="TP1 Rate" value={replaySummary?.tp1Rate != null ? `${replaySummary.tp1Rate}%` : "—"} tone="gold" />
          <MetaBox label="Activated Trades" value={replaySummary?.activatedTrades != null ? String(replaySummary.activatedTrades) : "—"} tone="gold" />
          <MetaBox label="Best Profile (Gap)" value={learningMap.gap_liquidity_sweep_reclaim ? `${learningMap.gap_liquidity_sweep_reclaim.win_rate.toFixed(0)}% wr` : "—"} tone="gold" />
          <MetaBox label="Best Profile (Engulfing)" value={learningMap.engulfing_rejection ? `${learningMap.engulfing_rejection.win_rate.toFixed(0)}% wr` : "—"} tone="gold" />
          <MetaBox label="Best Profile (Break/Retest)" value={learningMap.standard_break_retest ? `${learningMap.standard_break_retest.win_rate.toFixed(0)}% wr` : "—"} tone="gold" />
          <MetaBox label="Win Rate (live)" value={`${analytics.data?.metrics?.win_rate ?? 0}%`} tone={(analytics.data?.metrics?.win_rate ?? 0) >= 50 ? "buy" : "gold"} />
        </CardContent>
      </Card>

      {/* G2. Session Liquidity Intelligence Card */}
      <SessionLiquidityCard liquidity={(data?.sessionLiquidity ?? {}) as SessionLiquidityState} />

      {/* H. System Health Card */}
      <SystemHealthCard health={health} dataHealth={dataHealth} systemHealth={systemHealth} telegramHealth={telegramHealth.data ?? null} />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <FeedCard
          title="Recent Signals"
          actionLabel="View all"
          actionTo="/signals"
          loading={signals.isLoading}
          error={signals.error instanceof Error ? signals.error.message : null}
          empty={!signals.data?.signals.length}
          emptyLabel="No signals available yet."
        >
          {signals.data?.signals.map((signal) => (
            <div key={signal.id} className="flex items-center justify-between rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-2.5">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Badge variant={signal.type === "Gap" ? "gold" : "outline"} className="text-[10px]">{signal.type}</Badge>
                  <span className={cn("flex items-center gap-1 text-xs font-semibold", signal.direction === "BUY" ? "text-buy" : "text-sell")}>
                    {signal.direction === "BUY" ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
                    {signal.direction}
                  </span>
                </div>
                <div className="num text-xs text-foreground">{signal.price?.toFixed(2) ?? "--"}</div>
                <div className="text-[10px] text-muted-foreground">{signal.basis} | {signal.timeframe} | {signal.session_name ?? "session n/a"}</div>
              </div>
              <div className="text-right">
                <div className="num text-sm font-bold text-gold-300">Q{signal.quality}</div>
                <div className="text-[10px] text-muted-foreground">{signal.status}</div>
              </div>
            </div>
          ))}
        </FeedCard>
        <FeedCard
          title="Active Trades"
          actionLabel="Open trades"
          actionTo="/trades"
          loading={trades.isLoading}
          error={trades.error instanceof Error ? trades.error.message : null}
          empty={!trades.data?.trades.length}
          emptyLabel="No active trades right now."
        >
          {trades.data?.trades.map((trade) => (
            <div key={trade.id} className="rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-2.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="num text-xs font-bold text-foreground">{trade.pair}</span>
                  <span className={cn("flex items-center gap-1 text-xs font-semibold", trade.direction === "BUY" ? "text-buy" : "text-sell")}>
                    {trade.direction === "BUY" ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
                    {trade.direction}
                  </span>
                </div>
                <Badge variant="gold" className="text-[10px]">{trade.status}</Badge>
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] text-muted-foreground">
                <span>Entry <span className="num text-foreground">{trade.entry_price?.toFixed(2) ?? "--"}</span></span>
                <span>TP1 <span className="num text-buy">{trade.tp1?.toFixed(2) ?? "--"}</span></span>
                <span>Pips <span className={cn("num", (trade.realized_pips ?? 0) >= 0 ? "text-buy" : "text-sell")}>{trade.realized_pips?.toFixed(1) ?? "--"}</span></span>
              </div>
            </div>
          ))}
        </FeedCard>
        <FeedCard
          title="Recent Alerts"
          actionLabel="Open alerts"
          actionTo="/alerts"
          loading={alerts.isLoading}
          error={alerts.error instanceof Error ? alerts.error.message : null}
          empty={!alerts.data?.alerts.length}
          emptyLabel="No alerts in the feed."
        >
          {alerts.data?.alerts.map((alert) => (
            <div key={alert.id} className="rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-2.5">
              <div className="flex items-center gap-2">
                <Bell className={cn("h-3.5 w-3.5", alert.priority === "critical" ? "text-sell" : alert.priority === "high" ? "text-warn" : "text-gold-300")} />
                <span className="text-xs font-semibold text-foreground">{alert.title}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">{alert.message}</p>
              <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
                <span>{alert.related_label}</span>
                <span>{new Date(alert.timestamp).toLocaleString()}</span>
              </div>
            </div>
          ))}
        </FeedCard>
      </div>

      {/* I. Developer Diagnostics (collapsed by default) */}
      <DeveloperDiagnosticsAccordion diagnostics={developerDiag} />
    </div>
  )
}

function SessionLiquidityCard({ liquidity }: { liquidity: SessionLiquidityState }) {
  const enabled = Boolean(liquidity?.enabled)
  const sessions = liquidity?.sessions ?? {}
  const asia = sessions.asia ?? {}
  const london = sessions.london ?? {}
  const ny = sessions.new_york ?? {}
  const setups = liquidity?.setups ?? []
  const topSetup = setups[0]

  const findLevel = (level_type: string) =>
    (liquidity?.levels ?? []).find((lvl) => lvl.level_type === level_type)
  const stateFor = (level_type: string) => {
    const lvl = findLevel(level_type)
    if (!lvl) return "unknown"
    if (lvl.swept && lvl.reclaimed) return "swept/reclaimed"
    if (lvl.swept) return "swept"
    return "unswept"
  }
  const labelFor = (level_type: string) => findLevel(level_type)?.quality_label ?? "n/a"

  const renderRow = (label: string, level_type: string, value?: number | null) => (
    <InfoRow
      label={label}
      value={value ? `${value.toFixed(2)} - ${stateFor(level_type)} - ${labelFor(level_type)}` : "n/a"}
      mono
    />
  )

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-purple-400/25 bg-purple-500/12 text-purple-200">
            <Layers className="h-4 w-4" />
          </div>
          <CardTitle>Session Liquidity Intelligence</CardTitle>
          <Badge variant={enabled ? "buy" : "outline"} className="ml-auto text-[10px]">
            {enabled ? (liquidity?.advisory_only ? "Advisory" : "Blocking") : "Disabled"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
          <div className="text-xs font-semibold text-gold-300">Session Levels</div>
          {renderRow("Asian High", "asian_high", asia.high)}
          {renderRow("Asian Low", "asian_low", asia.low)}
          {renderRow("London High", "london_high", london.high)}
          {renderRow("London Low", "london_low", london.low)}
          {renderRow("New York High", "new_york_high", ny.high)}
          {renderRow("New York Low", "new_york_low", ny.low)}
          {renderRow("Previous Day High", "previous_day_high", liquidity?.previous_day_high)}
          {renderRow("Previous Day Low", "previous_day_low", liquidity?.previous_day_low)}
        </div>
        <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
          <div className="text-xs font-semibold text-gold-300">Liquidity Read</div>
          <InfoRow
            label="Nearest Buy-side"
            value={liquidity?.nearest_buy_side_liquidity ? Number(liquidity.nearest_buy_side_liquidity).toFixed(2) : "n/a"}
            mono
          />
          <InfoRow
            label="Nearest Sell-side"
            value={liquidity?.nearest_sell_side_liquidity ? Number(liquidity.nearest_sell_side_liquidity).toFixed(2) : "n/a"}
            mono
          />
          <InfoRow label="Liquidity Bias" value={liquidity?.liquidity_bias ?? "balanced"} />
          <InfoRow label="Expected Play" value={liquidity?.expected_play ?? "—"} />
          <Separator />
          <div className="text-xs font-semibold text-gold-300">Active Setup</div>
          {topSetup ? (
            <>
              <InfoRow label="Setup" value={String(topSetup.setup_type ?? "—").replace(/_/g, " ")} />
              <InfoRow label="Direction" value={topSetup.direction ?? "—"} />
              <InfoRow
                label="Swept Level"
                value={
                  topSetup.swept_level
                    ? `${String(topSetup.swept_level_type ?? "").replace(/_/g, " ")} ${Number(topSetup.swept_level).toFixed(2)}`
                    : "—"
                }
                mono
              />
              <InfoRow label="Trap Quality" value={topSetup.quality_label ?? "—"} />
              <InfoRow
                label="Targets"
                value={(topSetup.target_levels ?? []).map((v) => Number(v).toFixed(2)).join(" / ") || "—"}
                mono
              />
              <InfoRow label="Notes" value={topSetup.notes ?? "—"} />
            </>
          ) : (
            <div className="text-[11px] text-muted-foreground">No active sweep setup. Watching session pools.</div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function SystemHealthCard({
  health,
  dataHealth,
  systemHealth,
  telegramHealth,
}: {
  health: ReturnType<typeof useHealth>
  dataHealth: ReturnType<typeof useDataHealth>
  systemHealth: SystemHealthState
  telegramHealth: TelegramHealthResponse | null
}) {
  const tgConnected = systemHealth.telegramConnected ?? telegramHealth?.connected ?? false
  const tgConfigured = systemHealth.telegramConfigured ?? telegramHealth?.configured ?? false
  const tgLastStatus = systemHealth.telegramLastSendStatus ?? telegramHealth?.lastSendStatus ?? "unknown"
  const tgLastError = systemHealth.telegramLastError ?? telegramHealth?.lastError ?? null
  const tgConfigErrors = telegramHealth?.configErrors ?? systemHealth.telegramConfigErrors ?? []
  const tgConfigWarnings = telegramHealth?.configWarnings ?? systemHealth.telegramConfigWarnings ?? []
  const startupFailed = tgLastStatus !== "success" && (telegramHealth?.alertTypeCounts?.["STARTUP"] ?? 0) === 0 && (telegramHealth?.failureCount ?? 0) > 0
  const tgLabel = !tgConfigured
    ? "Not configured"
    : tgConnected
    ? "Connected"
    : tgLastStatus === "config_invalid"
    ? "Config invalid"
    : tgLastStatus === "duplicate_suppressed"
    ? "Duplicate suppressed"
    : "Failed"
  const tgVariant = tgConnected ? "buy" : !tgConfigured ? "outline" : "sell"
  const dataHealthHasWarnings = (dataHealth.data?.warnings?.length ?? 0) > 0

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-buy/20 bg-buy/10 text-buy">
            <Activity className="h-4 w-4" />
          </div>
          <CardTitle>System Health</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
          <div className="text-xs font-semibold text-gold-300">Engine & Data</div>
          <InfoRow label="Engine" value={health.data?.status ?? "unknown"} />
          <InfoRow label="Supabase" value={(systemHealth.supabaseConnected ?? health.data?.db_connected) ? "Connected" : "Unavailable"} />
          <InfoRow label="MT5 / Data Feed" value={systemHealth.dataFeedStatus ?? "unknown"} />
          <InfoRow label="Data Health" value={dataHealthHasWarnings ? "Warnings" : "Healthy"} />
          <InfoRow label="Last Error" value={systemHealth.lastError ?? "None"} />
          <InfoRow label="Alert Suppression" value={systemHealth.alertSuppressionStatus ?? "—"} />
        </div>
        <div className="space-y-2 rounded-lg border border-ap-border bg-ap-surface/35 p-4">
          <div className="flex items-center justify-between">
            <div className="text-xs font-semibold text-gold-300">Telegram</div>
            <Badge variant={tgVariant} className="text-[10px]">{tgLabel}</Badge>
          </div>
          {startupFailed ? (
            <div className="rounded-md border border-sell/30 bg-sell/10 px-2 py-2 text-[11px] text-sell">
              <div className="font-semibold">Telegram failed: STARTUP</div>
              <div className="text-[10px]">Reason: {tgLastError ?? tgLastStatus}</div>
            </div>
          ) : null}
          <InfoRow label="Bot Username" value={systemHealth.telegramBotUsername ?? telegramHealth?.botUsername ?? "—"} />
          <InfoRow label="Last Send Status" value={tgLastStatus} />
          <InfoRow label="Last Alert Type" value={systemHealth.telegramLastAlertType ?? telegramHealth?.lastAlertType ?? "—"} />
          <InfoRow label="Last Success" value={formatRelative(systemHealth.telegramLastSuccessAt ?? telegramHealth?.lastSuccessAt)} />
          <InfoRow label="Last Failure" value={formatRelative(systemHealth.telegramLastFailureAt ?? telegramHealth?.lastFailureAt)} />
          <InfoRow label="Last Error" value={tgLastError ?? "None"} />
          <InfoRow label="Failure Count" value={String(systemHealth.telegramFailureCount ?? telegramHealth?.failureCount ?? 0)} mono />
          {tgConfigErrors.length ? (
            <div className="rounded-md border border-sell/30 bg-sell/10 px-2 py-2 text-[10px] text-sell">
              <div className="font-semibold">Config errors:</div>
              {tgConfigErrors.map((err, i) => (
                <div key={i}>• {err}</div>
              ))}
            </div>
          ) : null}
          {tgConfigWarnings.length ? (
            <div className="rounded-md border border-warn/30 bg-warn/10 px-2 py-2 text-[10px] text-warn">
              <div className="font-semibold">Config warnings:</div>
              {tgConfigWarnings.map((warn, i) => (
                <div key={i}>• {warn}</div>
              ))}
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

function DeveloperDiagnosticsAccordion({ diagnostics }: { diagnostics: DeveloperDiagnosticsState }) {
  const [open, setOpen] = useState(false)
  return (
    <Card>
      <CardHeader className="pb-3">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center justify-between gap-3"
        >
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-ap-border bg-ap-surface/35 text-muted-foreground">
              <Wrench className="h-4 w-4" />
            </div>
            <CardTitle>Developer Diagnostics</CardTitle>
            <Badge variant="outline" className="text-[10px]">Debug only</Badge>
          </div>
          {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
        </button>
      </CardHeader>
      {open ? (
        <CardContent className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-4">
          <MetaBox label="Active Instance ID" value={diagnostics.activeInstanceId ?? "—"} tone="gold" />
          <MetaBox label="Last Scan Result" value={diagnostics.lastScanResult ?? "—"} tone="gold" />
          <MetaBox label="Candidates Found" value={String(diagnostics.candidatesFound ?? 0)} tone="gold" />
          <MetaBox label="Alerts Sent" value={String(diagnostics.rawAlertsSent ?? 0)} tone="gold" />
          <MetaBox label="Alerts Failed" value={String(diagnostics.rawAlertsFailed ?? 0)} tone={(diagnostics.rawAlertsFailed ?? 0) > 0 ? "sell" : "gold"} />
          <MetaBox label="Reject Reason" value={diagnostics.rejectReason ?? "—"} tone="gold" />
          <MetaBox label="Total Scans" value={String(diagnostics.totalScans ?? 0)} tone="gold" />
          <MetaBox label="Total Candidates" value={String(diagnostics.totalCandidatesFound ?? 0)} tone="gold" />
          <MetaBox label="Total Alerts Sent" value={String(diagnostics.totalAlertsSent ?? 0)} tone="gold" />
          <MetaBox label="Total Alerts Failed" value={String(diagnostics.totalAlertsFailed ?? 0)} tone={(diagnostics.totalAlertsFailed ?? 0) > 0 ? "sell" : "gold"} />
          <MetaBox label="Duplicates Blocked" value={String(diagnostics.totalDuplicatesBlocked ?? 0)} tone="gold" />
          <MetaBox label="Duplicate Keys" value={String(diagnostics.duplicateSuppressionKeys ?? 0)} tone="gold" />
          <MetaBox label="Last Scan #" value={String(diagnostics.lastScanNumber ?? 0)} tone="gold" />
          <MetaBox label="Session Blocking" value={String(Boolean(diagnostics.sessionBlocking))} tone="gold" />
          <MetaBox label="Scan Allowed" value={String(diagnostics.scanAllowed ?? true)} tone="gold" />
          <MetaBox label="Background Tasks" value={String(diagnostics.backgroundTasksActive ?? 0)} tone="gold" />
          <MetaBox label="Runtime Alerts" value={String(Boolean(diagnostics.runtimeAlertsEnabled))} tone="gold" />
          <MetaBox label="Last Shutdown" value={diagnostics.lastShutdownTime ?? "—"} tone="gold" />
        </CardContent>
      ) : null}
    </Card>
  )
}

function FeedCard({
  title,
  actionLabel,
  actionTo,
  loading,
  error,
  empty,
  emptyLabel,
  children,
}: {
  title: string
  actionLabel: string
  actionTo: string
  loading: boolean
  error: string | null
  empty: boolean
  emptyLabel: string
  children: React.ReactNode
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>{title}</CardTitle>
          <Button asChild variant="ghost" size="sm">
            <Link to={actionTo}>{actionLabel}</Link>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        {loading ? <StateBlock icon={Activity} label="Loading live data..." /> : null}
        {!loading && error ? <StateBlock icon={AlertTriangle} label={error} tone="error" /> : null}
        {!loading && !error && empty ? <StateBlock icon={Layers} label={emptyLabel} /> : null}
        {!loading && !error && !empty ? children : null}
      </CardContent>
    </Card>
  )
}

function StateBlock({ icon: Icon, label, tone = "muted" }: { icon: typeof Activity; label: string; tone?: "muted" | "error" }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-4">
      <Icon className={cn("h-4 w-4", tone === "error" ? "text-sell" : "text-muted-foreground")} />
      <span className={cn("text-sm", tone === "error" ? "text-sell" : "text-muted-foreground")}>{label}</span>
    </div>
  )
}

function InfoRow({ label, value, mono, valueClass }: { label: string; value: string; mono?: boolean; valueClass?: string }) {
  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] text-muted-foreground">{label}</span>
        <span className={cn("text-[10px] text-foreground text-right", mono ? "font-mono" : "", valueClass)}>{value}</span>
      </div>
      <Separator />
    </>
  )
}

function MetaBox({ label, value, tone }: { label: string; value: string; tone: "gold" | "buy" | "sell" }) {
  const color = tone === "buy" ? "text-buy" : tone === "sell" ? "text-sell" : "text-gold-300"
  return (
    <div className="rounded-lg border border-ap-border bg-ap-surface/35 px-3 py-3">
      <div className="label-xs">{label}</div>
      <div className={cn("num mt-2 text-sm font-bold", color)}>{value}</div>
    </div>
  )
}

function formatPercent(value: unknown) {
  const numeric = typeof value === "number" ? value : Number(value)
  if (!Number.isFinite(numeric)) return "—"
  // Accept either 0-1 or 0-100
  const pct = Math.abs(numeric) <= 1 ? numeric * 100 : numeric
  return `${pct.toFixed(1)}%`
}

function formatSignedNumber(value: unknown) {
  const numeric = typeof value === "number" ? value : Number(value)
  if (!Number.isFinite(numeric)) return "—"
  return `${numeric >= 0 ? "+" : ""}${numeric.toFixed(1)}`
}

function formatMaybe(value?: number | null, digits = 2) {
  if (value == null) return "—"
  const numeric = typeof value === "number" ? value : Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : "—"
}

function formatLevelIntel(row?: { level?: number | null; score?: number | null; quality_label?: string | null; state?: string | null } | null) {
  if (!row || row.level == null) return "-"
  const level = Number(row.level)
  const score = row.score != null ? Number(row.score).toFixed(0) : "0"
  return `${Number.isFinite(level) ? level.toFixed(2) : "-"} (${score}, ${row.quality_label ?? row.state ?? "level"})`
}

function formatBiasLabel(value?: string | null) {
  return value ? value.replace(/_/g, " ") : "Unavailable"
}

function formatSession(value?: string | null) {
  if (!value) return "Quiet Session"
  if (value === "off_session" || value === "quiet_session") return "Quiet Session"
  if (value === "overlap") return "Overlap"
  if (value === "london") return "London"
  if (value === "new_york") return "New York"
  if (value === "asia") return "Asia"
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatRelative(value?: string | null) {
  if (!value) return "—"
  const date = new Date(value)
  if (isNaN(date.getTime())) return "—"
  const diff = Date.now() - date.getTime()
  if (diff < 0 || diff > 7 * 24 * 60 * 60 * 1000) return date.toLocaleString()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return date.toLocaleString()
}

function biasColor(value?: string | null) {
  if (value === "bullish") return "text-buy"
  if (value === "bearish") return "text-sell"
  return "text-gold-300"
}
