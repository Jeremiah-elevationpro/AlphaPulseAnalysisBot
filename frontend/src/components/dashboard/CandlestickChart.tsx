import { useMemo } from "react"

import type { Candle } from "@/lib/api"
import { cn } from "@/lib/utils"

export interface ChartLevel {
  price: number
  label: string
  color: string
  dashed?: boolean
}

export interface ChartZone {
  low: number
  high: number
  label: string
  color: string
}

interface CandlestickChartProps {
  candles: Candle[]
  height?: number
  levels?: ChartLevel[]
  zones?: ChartZone[]
  currentPrice?: number | null
  className?: string
}

const PADDING = { top: 12, right: 64, bottom: 24, left: 8 }

export function CandlestickChart({
  candles,
  height = 320,
  levels = [],
  zones = [],
  currentPrice,
  className,
}: CandlestickChartProps) {
  const valid = useMemo(
    () =>
      candles.filter(
        (c): c is Required<Pick<Candle, "open" | "high" | "low" | "close">> & Candle =>
          c.open != null && c.high != null && c.low != null && c.close != null
      ),
    [candles]
  )

  if (valid.length === 0) {
    return (
      <div
        className={cn(
          "flex h-[320px] w-full items-center justify-center rounded-lg border border-ap-border bg-ap-surface/35 text-xs text-muted-foreground",
          className
        )}
        style={{ height }}
      >
        Candles not available yet. Spencer is still receiving price updates.
      </div>
    )
  }

  // Tight fixed width via viewBox; SVG scales to container width.
  const width = Math.max(640, valid.length * 8 + PADDING.left + PADDING.right)
  const innerW = width - PADDING.left - PADDING.right
  const innerH = height - PADDING.top - PADDING.bottom

  const allPrices: number[] = []
  for (const c of valid) {
    allPrices.push(c.high as number, c.low as number)
  }
  for (const lvl of levels) {
    if (Number.isFinite(lvl.price)) allPrices.push(lvl.price)
  }
  for (const zone of zones) {
    if (Number.isFinite(zone.low)) allPrices.push(zone.low)
    if (Number.isFinite(zone.high)) allPrices.push(zone.high)
  }
  if (currentPrice != null && Number.isFinite(currentPrice)) {
    allPrices.push(currentPrice)
  }

  let minPrice = Math.min(...allPrices)
  let maxPrice = Math.max(...allPrices)
  if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice) || minPrice === maxPrice) {
    minPrice = (currentPrice ?? valid[0]?.close ?? 0) - 1
    maxPrice = (currentPrice ?? valid[0]?.close ?? 0) + 1
  }
  const pricePad = (maxPrice - minPrice) * 0.04
  minPrice -= pricePad
  maxPrice += pricePad
  const priceRange = maxPrice - minPrice

  const candleSlot = innerW / valid.length
  const candleW = Math.max(2, candleSlot * 0.6)

  const yFor = (price: number) =>
    PADDING.top + ((maxPrice - price) / priceRange) * innerH
  const xFor = (i: number) => PADDING.left + i * candleSlot + candleSlot / 2

  // Y-axis ticks
  const tickCount = 5
  const ticks = Array.from({ length: tickCount }, (_, i) => {
    const t = minPrice + (priceRange * i) / (tickCount - 1)
    return { value: t, y: yFor(t) }
  })

  return (
    <div className={cn("w-full overflow-x-auto", className)}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="block w-full"
        style={{ height }}
      >
        {/* Background grid + axis ticks */}
        {ticks.map((t) => (
          <g key={`tick-${t.value}`}>
            <line
              x1={PADDING.left}
              x2={width - PADDING.right}
              y1={t.y}
              y2={t.y}
              stroke="rgba(148, 163, 184, 0.08)"
              strokeWidth={1}
            />
            <text
              x={width - PADDING.right + 6}
              y={t.y + 3}
              fontSize={10}
              fill="rgba(148, 163, 184, 0.7)"
              fontFamily="ui-monospace, monospace"
            >
              {t.value.toFixed(2)}
            </text>
          </g>
        ))}

        {/* Zones */}
        {zones.map((zone, i) => {
          const top = yFor(Math.max(zone.high, zone.low))
          const bottom = yFor(Math.min(zone.high, zone.low))
          const h = Math.max(1, bottom - top)
          return (
            <g key={`zone-${i}`}>
              <rect
                x={PADDING.left}
                y={top}
                width={width - PADDING.left - PADDING.right}
                height={h}
                fill={zone.color}
                opacity={0.18}
              />
              <text
                x={PADDING.left + 8}
                y={top + 11}
                fontSize={9}
                fill={zone.color}
                fontFamily="ui-sans-serif, system-ui"
              >
                {zone.label}
              </text>
            </g>
          )
        })}

        {/* Candles */}
        {valid.map((c, i) => {
          const o = c.open as number
          const h = c.high as number
          const l = c.low as number
          const cl = c.close as number
          const isBull = cl >= o
          const color = isBull ? "#22c55e" : "#ef4444"
          const x = xFor(i)
          const yHigh = yFor(h)
          const yLow = yFor(l)
          const yOpen = yFor(o)
          const yClose = yFor(cl)
          const bodyTop = Math.min(yOpen, yClose)
          const bodyH = Math.max(1, Math.abs(yOpen - yClose))
          return (
            <g key={`c-${i}-${c.time ?? i}`}>
              <line
                x1={x}
                x2={x}
                y1={yHigh}
                y2={yLow}
                stroke={color}
                strokeWidth={1}
              />
              <rect
                x={x - candleW / 2}
                y={bodyTop}
                width={candleW}
                height={bodyH}
                fill={color}
                opacity={isBull ? 0.85 : 0.95}
              />
            </g>
          )
        })}

        {/* Price levels */}
        {levels.map((lvl, i) => {
          if (!Number.isFinite(lvl.price)) return null
          const y = yFor(lvl.price)
          return (
            <g key={`lvl-${i}-${lvl.label}`}>
              <line
                x1={PADDING.left}
                x2={width - PADDING.right}
                y1={y}
                y2={y}
                stroke={lvl.color}
                strokeWidth={1.2}
                strokeDasharray={lvl.dashed ? "4 3" : undefined}
                opacity={0.85}
              />
              <rect
                x={width - PADDING.right + 1}
                y={y - 7}
                width={60}
                height={14}
                fill={lvl.color}
                opacity={0.85}
                rx={2}
              />
              <text
                x={width - PADDING.right + 5}
                y={y + 3}
                fontSize={9}
                fill="#0b0f17"
                fontFamily="ui-sans-serif, system-ui"
                fontWeight={600}
              >
                {lvl.label} {lvl.price.toFixed(2)}
              </text>
            </g>
          )
        })}

        {/* Current price marker */}
        {currentPrice != null && Number.isFinite(currentPrice) ? (
          <g>
            <line
              x1={PADDING.left}
              x2={width - PADDING.right}
              y1={yFor(currentPrice)}
              y2={yFor(currentPrice)}
              stroke="#fbbf24"
              strokeWidth={1}
              strokeDasharray="2 3"
            />
            <rect
              x={width - PADDING.right + 1}
              y={yFor(currentPrice) - 7}
              width={60}
              height={14}
              fill="#fbbf24"
              rx={2}
            />
            <text
              x={width - PADDING.right + 5}
              y={yFor(currentPrice) + 3}
              fontSize={9}
              fill="#0b0f17"
              fontFamily="ui-monospace, monospace"
              fontWeight={700}
            >
              PX {currentPrice.toFixed(2)}
            </text>
          </g>
        ) : null}
      </svg>
    </div>
  )
}
