import { Bot, Bell, Palette, Sliders, Save } from "lucide-react"
import { useState } from "react"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"


export default function Settings() {
  return (
    <div className="p-4 md:p-6 space-y-5">
      <div>
        <h2 className="text-lg font-bold text-foreground">Settings</h2>
        <p className="text-sm text-muted-foreground">Bot configuration and preferences</p>
      </div>

      <Tabs defaultValue="bot">
        <TabsList className="flex-wrap h-auto">
          <TabsTrigger value="bot" className="gap-1.5"><Bot className="w-3 h-3" />Bot</TabsTrigger>
          <TabsTrigger value="filters" className="gap-1.5"><Sliders className="w-3 h-3" />Filters</TabsTrigger>
          <TabsTrigger value="notifications" className="gap-1.5"><Bell className="w-3 h-3" />Alerts</TabsTrigger>
          <TabsTrigger value="display" className="gap-1.5"><Palette className="w-3 h-3" />Display</TabsTrigger>
        </TabsList>

        {/* ── Bot Tab ─────────────────────────────── */}
        <TabsContent value="bot">
          <div className="space-y-4">
            <SettingsCard title="Trading Pair" description="Active symbol and timeframe configuration">
              <SettingRow label="Symbol" description="MT5 instrument symbol">
                <Input defaultValue="XAUUSD" className="w-36 font-mono" />
              </SettingRow>
              <SettingRow label="Higher Timeframe" description="Detection / analysis timeframe">
                <Select options={["M15", "M30", "H1", "H4"]} defaultValue="M30" />
              </SettingRow>
              <SettingRow label="Lower Timeframe" description="Confirmation and entry timeframe">
                <Select options={["M5", "M15", "M30"]} defaultValue="M15" />
              </SettingRow>
              <SettingRow label="Pip Size" description="Instrument pip size (0.01 = standard)">
                <Input defaultValue="0.01" className="w-24 font-mono" />
              </SettingRow>
            </SettingsCard>

            <SettingsCard title="Bot Behaviour" description="Control how the bot analyzes and generates signals">
              <SettingRow label="Auto-Trade Mode" description="Automatically execute confirmed signals via MT5">
                <Switch />
              </SettingRow>
              <SettingRow label="Signal Generation" description="Run the detection pipeline on new candle close">
                <Switch defaultChecked />
              </SettingRow>
              <SettingRow label="Historical Replay" description="Enable replay engine for backtesting">
                <Switch />
              </SettingRow>
              <SettingRow label="RL Learning" description="Train the reinforcement learning engine on completed trades">
                <Switch defaultChecked />
              </SettingRow>
            </SettingsCard>
          </div>
        </TabsContent>

        {/* ── Filters Tab ─────────────────────────── */}
        <TabsContent value="filters">
          <div className="space-y-4">
            <SettingsCard title="A/V Level Filters" description="Quality thresholds for reversal origin levels">
              <SettingRow label="Min Displacement (pips)" description="Minimum forward impulse after level formation">
                <Input defaultValue="30" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="Min Distance from Price (pips)" description="Minimum distance between level and current price">
                <Input defaultValue="15" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="Max Break Count (hard)" description="Hard reject ceiling for level break count">
                <Input defaultValue="10" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="Max Break Count (soft)" description="Threshold above which a penalty is applied">
                <Input defaultValue="6" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="Max Touch Count (hard)" description="Hard reject ceiling for touch count">
                <Input defaultValue="12" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="Min Body Size (hard, pips)" description="Hard reject for tiny body candles">
                <Input defaultValue="2.0" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="Mid-Range Penalty" description="Score penalty for levels in the middle of range">
                <Input defaultValue="12" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="Broken Level Penalty" description="Score penalty for levels where price has crossed">
                <Input defaultValue="12" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="A/V Diversity Rule" description="Inject one A/V if shortlist has none">
                <Switch defaultChecked />
              </SettingRow>
            </SettingsCard>

            <SettingsCard title="Quality Scoring" description="Bonus and penalty weights for the 100-point scoring model">
              <SettingRow label="Origin + Displacement Bonus" description="Bonus for origin candle + 50p+ displacement">
                <Input defaultValue="8" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="Strong Origin Bonus (selector)" description="Extra score for high wick ratio in selection">
                <Input defaultValue="8" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="QM Context Bonus" description="Bonus when level aligns with QM structure">
                <Input defaultValue="5" className="w-24 font-mono" />
              </SettingRow>
              <SettingRow label="Micro Confirmation Bonus" description="Bonus for A/V with micro confirmation">
                <Input defaultValue="10" className="w-24 font-mono" />
              </SettingRow>
            </SettingsCard>
          </div>
        </TabsContent>

        {/* ── Notifications Tab ───────────────────── */}
        <TabsContent value="notifications">
          <div className="space-y-4">
            <SettingsCard title="Telegram" description="Configure Telegram bot notifications">
              <SettingRow label="Enable Telegram Alerts" description="Send signal notifications to Telegram">
                <Switch defaultChecked />
              </SettingRow>
              <SettingRow label="Bot Token" description="Your Telegram bot API token">
                <Input type="password" defaultValue="••••••••••••••••" className="w-48 font-mono" />
              </SettingRow>
              <SettingRow label="Chat ID" description="Target Telegram chat or channel ID">
                <Input defaultValue="-100123456789" className="w-40 font-mono" />
              </SettingRow>
              <SettingRow label="Signal Alerts" description="Alert on each new confirmed signal">
                <Switch defaultChecked />
              </SettingRow>
              <SettingRow label="News Warnings" description="Warn before high-impact economic events">
                <Switch defaultChecked />
              </SettingRow>
              <SettingRow label="Trade Closures" description="Alert when trades are closed with P&L">
                <Switch defaultChecked />
              </SettingRow>
            </SettingsCard>
          </div>
        </TabsContent>

        {/* ── Display Tab ─────────────────────────── */}
        <TabsContent value="display">
          <div className="space-y-4">
            <SettingsCard title="Interface" description="Customize the dashboard appearance">
              <SettingRow label="Compact Mode" description="Reduce padding for denser information layout">
                <Switch />
              </SettingRow>
              <SettingRow label="Price Format" description="How prices are displayed in the UI">
                <Select options={["2,287.50", "2287.50", "2287.5"]} defaultValue="2,287.50" />
              </SettingRow>
              <SettingRow label="Sidebar Default" description="Sidebar expanded or collapsed on load">
                <Select options={["Expanded", "Collapsed"]} defaultValue="Expanded" />
              </SettingRow>
            </SettingsCard>

            <SettingsCard title="About" description="System information">
              <div className="space-y-3 py-1">
                {[
                  ["Version", "AlphaPulse v1.0.0"],
                  ["Strategy", "M30→M15 A/V/Gap"],
                  ["Model", "Quality-First v2"],
                  ["Build", "Apr 23, 2026"],
                ].map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">{k}</span>
                    <span className="num text-xs font-semibold text-foreground">{v}</span>
                  </div>
                ))}
              </div>
            </SettingsCard>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}

// ── Reusable sub-components ──────────────────────────────────────────

function SettingsCard({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-0 divide-y divide-ap-border pt-2">
        {children}
      </CardContent>
      <CardFooter className="justify-end">
        <Button size="sm" className="gap-1.5">
          <Save className="w-3.5 h-3.5" />
          Save Changes
        </Button>
      </CardFooter>
    </Card>
  )
}

function SettingRow({
  label,
  description,
  children,
}: {
  label: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-3.5">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{label}</p>
        {description && <p className="text-xs text-muted-foreground mt-0.5">{description}</p>}
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  )
}

function Select({ options, defaultValue }: { options: string[]; defaultValue: string }) {
  const [value, setValue] = useState(defaultValue)
  return (
    <select
      value={value}
      onChange={(e) => setValue(e.target.value)}
      className={cn(
        "h-9 rounded-md border border-ap-border bg-ap-surface px-3 text-sm text-foreground",
        "focus:outline-none focus:ring-2 focus:ring-ring focus:border-gold-600",
        "transition-colors font-mono"
      )}
    >
      {options.map((o) => (
        <option key={o} value={o} className="bg-ap-card">
          {o}
        </option>
      ))}
    </select>
  )
}
