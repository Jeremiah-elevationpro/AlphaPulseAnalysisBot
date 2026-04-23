export type LevelType = "A" | "V" | "Gap"
export type Direction = "BUY" | "SELL"
export type TradeStatus = "open" | "closed" | "pending"
export type SignalStatus = "active" | "triggered" | "expired"
export type BotStatus = "online" | "offline" | "warning"
export type TimeFrame = "M15" | "M30" | "H1" | "H4" | "D1"
export type H4Bias = "bullish" | "bearish" | "neutral"
export type AlertSeverity = "info" | "warning" | "critical"

export interface Level {
  id: string
  type: LevelType
  price: number
  quality: number
  displacement: number
  touchCount: number
  breakCount: number
  basis: "origin-based" | "wick-based" | "imbalance"
  timeframe: TimeFrame
  createdAt: string
  status: SignalStatus
  direction: Direction
}

export interface Signal {
  id: string
  level: Level
  setupScore: number
  confirmationScore: number
  entryPrice: number
  sl: number
  tp: number
  riskReward: number
  createdAt: string
  status: SignalStatus
}

export interface Trade {
  id: string
  signal: Pick<Signal, "id" | "entryPrice" | "sl" | "tp" | "riskReward">
  direction: Direction
  levelType: LevelType
  openPrice: number
  closePrice?: number
  pnl?: number
  pips?: number
  status: TradeStatus
  openedAt: string
  closedAt?: string
}

export interface Alert {
  id: string
  severity: AlertSeverity
  message: string
  detail?: string
  createdAt: string
  read: boolean
}

export interface MarketSnapshot {
  price: number
  change: number
  changePct: number
  high24h: number
  low24h: number
  spread: number
  bias: H4Bias
  session: "sydney" | "tokyo" | "london" | "newyork"
}
