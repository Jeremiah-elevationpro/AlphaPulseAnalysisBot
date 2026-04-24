import { AnimatePresence, motion } from "framer-motion"
import { NavLink } from "react-router-dom"
import {
  LayoutDashboard,
  Zap,
  Crosshair,
  BarChart2,
  Bell,
  TrendingUp,
  Settings,
  ChevronLeft,
  ChevronRight,
  Activity,
  Cpu,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { useSidebar } from "@/hooks/use-sidebar"
import { StatusDot } from "@/components/ui/status-dot"

interface NavItem {
  to: string
  icon: React.ElementType
  label: string
  end?: boolean
}

const NAV_MAIN: NavItem[] = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard", end: true },
  { to: "/signals", icon: Zap, label: "Signals" },
  { to: "/setups", icon: Crosshair, label: "Manual Setups" },
  { to: "/trades", icon: BarChart2, label: "Trades" },
  { to: "/alerts", icon: Bell, label: "Alerts" },
  { to: "/analytics", icon: TrendingUp, label: "Analytics" },
]

const NAV_SYSTEM: NavItem[] = [{ to: "/settings", icon: Settings, label: "Settings" }]

export function Sidebar() {
  const { collapsed, toggle } = useSidebar()

  return (
    <motion.aside
      animate={{ width: collapsed ? 68 : 240 }}
      transition={{ duration: 0.28, ease: [0.4, 0, 0.2, 1] }}
      className="relative flex h-full flex-col overflow-hidden border-r border-ap-border bg-ap-sidebar/95 backdrop-blur-md"
    >
      <div className="flex h-14 flex-shrink-0 items-center border-b border-ap-border px-3.5">
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-gold-500/25 bg-gold-500/15 shadow-gold-xs">
          <Activity className="h-4 w-4 text-gold-300" />
        </div>
        <AnimatePresence initial={false}>
          {!collapsed && (
            <motion.div
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              transition={{ duration: 0.18 }}
              className="ml-2.5 overflow-hidden"
            >
              <div className="text-sm font-bold leading-tight text-foreground">FX Unfiltered</div>
              <div className="text-[10px] font-mono font-medium uppercase leading-tight tracking-[0.14em] text-gold-300">
                Powered By AlphaPulse
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <nav className="scrollbar-none flex-1 space-y-0.5 overflow-y-auto px-2 py-3">
        {!collapsed ? <p className="label-xs px-2 py-2 pb-1">Navigation</p> : null}
        {NAV_MAIN.map((item, i) => (
          <SidebarNavItem key={item.to} item={item} collapsed={collapsed} index={i} />
        ))}

        <div className="mt-2 border-t border-ap-border pt-3">
          {!collapsed ? <p className="label-xs px-2 py-2 pb-1">System</p> : null}
          {NAV_SYSTEM.map((item, i) => (
            <SidebarNavItem key={item.to} item={item} collapsed={collapsed} index={i} />
          ))}
        </div>
      </nav>

      <div className={cn("flex-shrink-0 border-t border-ap-border p-3", collapsed ? "flex justify-center" : "")}>
        {collapsed ? (
          <StatusDot status="online" pulse size="sm" />
        ) : (
          <div className="flex items-center gap-2.5 rounded-xl border border-ap-border bg-ap-surface/40 px-2.5 py-2">
            <StatusDot status="online" pulse size="sm" />
            <div className="min-w-0">
              <div className="text-xs font-semibold leading-tight text-foreground">Spencer Online</div>
              <div className="mt-0.5 text-[10px] font-mono leading-tight text-muted-foreground">Gap Only · XAUUSD</div>
            </div>
            <Cpu className="ml-auto h-3 w-3 flex-shrink-0 text-purple-300" />
          </div>
        )}
      </div>

      <button
        onClick={toggle}
        className={cn(
          "absolute -right-3 top-[52px] z-20 flex h-6 w-6 items-center justify-center rounded-full border border-ap-border-strong bg-ap-card text-muted-foreground shadow-card transition-colors duration-150",
          "hover:bg-ap-card-hover hover:text-foreground"
        )}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        {collapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronLeft className="h-3 w-3" />}
      </button>
    </motion.aside>
  )
}

function SidebarNavItem({
  item,
  collapsed,
  index,
}: {
  item: NavItem
  collapsed: boolean
  index: number
}) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.04, duration: 0.2 }}
    >
      <NavLink
        to={item.to}
        end={item.end}
        title={collapsed ? item.label : undefined}
        className={({ isActive }) =>
          cn(
            "group relative flex items-center rounded-lg border px-2.5 py-2 text-sm transition-all duration-150",
            collapsed ? "justify-center" : "gap-2.5",
            isActive
              ? "border-gold-500/20 bg-gradient-to-r from-purple-500/18 to-gold-500/10 text-gold-300"
              : "border-transparent text-muted-foreground hover:bg-ap-surface hover:text-foreground"
          )
        }
      >
        {({ isActive }) => (
          <>
            {isActive ? (
              <motion.div
                layoutId="nav-active-bar"
                className="absolute inset-y-0 left-0 w-0.5 rounded-full bg-gold-400"
                transition={{ type: "spring", stiffness: 500, damping: 35 }}
              />
            ) : null}
            <item.icon className={cn("h-4 w-4 flex-shrink-0", isActive ? "text-gold-300" : "text-muted-foreground group-hover:text-foreground")} />
            <AnimatePresence initial={false}>
              {!collapsed && (
                <motion.span
                  initial={{ opacity: 0, width: 0 }}
                  animate={{ opacity: 1, width: "auto" }}
                  exit={{ opacity: 0, width: 0 }}
                  transition={{ duration: 0.18 }}
                  className="truncate overflow-hidden whitespace-nowrap font-medium"
                >
                  {item.label}
                </motion.span>
              )}
            </AnimatePresence>
          </>
        )}
      </NavLink>
    </motion.div>
  )
}
