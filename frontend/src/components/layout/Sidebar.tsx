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

const NAV_SYSTEM: NavItem[] = [
  { to: "/settings", icon: Settings, label: "Settings" },
]

export function Sidebar() {
  const { collapsed, toggle } = useSidebar()

  return (
    <motion.aside
      animate={{ width: collapsed ? 68 : 240 }}
      transition={{ duration: 0.28, ease: [0.4, 0, 0.2, 1] }}
      className="relative flex flex-col h-full bg-ap-sidebar border-r border-ap-border flex-shrink-0 overflow-hidden"
    >
      {/* Brand */}
      <div className="flex items-center h-14 px-3.5 border-b border-ap-border flex-shrink-0">
        <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-gold-500/15 border border-gold-500/25 flex items-center justify-center shadow-gold-xs">
          <Activity className="w-4 h-4 text-gold-400" />
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
              <div className="text-sm font-bold text-foreground tracking-wide leading-tight">
                AlphaPulse
              </div>
              <div className="text-[10px] font-mono font-medium text-gold-500 tracking-[0.14em] leading-tight uppercase">
                XAUUSD Bot
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto scrollbar-none">
        {!collapsed && (
          <p className="label-xs px-2 py-2 pb-1">Navigation</p>
        )}
        {NAV_MAIN.map((item, i) => (
          <SidebarNavItem key={item.to} item={item} collapsed={collapsed} index={i} />
        ))}

        <div className="pt-3 mt-2 border-t border-ap-border">
          {!collapsed && (
            <p className="label-xs px-2 py-2 pb-1">System</p>
          )}
          {NAV_SYSTEM.map((item, i) => (
            <SidebarNavItem key={item.to} item={item} collapsed={collapsed} index={i} />
          ))}
        </div>
      </nav>

      {/* Bot Status Footer */}
      <div className={cn("flex-shrink-0 border-t border-ap-border p-3", collapsed ? "flex justify-center" : "")}>
        {collapsed ? (
          <StatusDot status="online" pulse size="sm" />
        ) : (
          <div className="flex items-center gap-2.5 px-1">
            <StatusDot status="online" pulse size="sm" />
            <div className="min-w-0">
              <div className="text-xs font-semibold text-foreground leading-tight">Bot Online</div>
              <div className="text-[10px] font-mono text-muted-foreground leading-tight mt-0.5">
                M30 → M15 · XAUUSD
              </div>
            </div>
            <Cpu className="w-3 h-3 text-muted-foreground ml-auto flex-shrink-0" />
          </div>
        )}
      </div>

      {/* Collapse toggle */}
      <button
        onClick={toggle}
        className={cn(
          "absolute top-[52px] -right-3 z-20",
          "w-6 h-6 rounded-full bg-ap-card border border-ap-border-strong",
          "flex items-center justify-center",
          "text-muted-foreground hover:text-foreground hover:bg-ap-card-hover",
          "transition-colors duration-150 shadow-card"
        )}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        {collapsed ? (
          <ChevronRight className="w-3 h-3" />
        ) : (
          <ChevronLeft className="w-3 h-3" />
        )}
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
            "relative flex items-center rounded-lg px-2.5 py-2 text-sm transition-all duration-150 group",
            collapsed ? "justify-center" : "gap-2.5",
            isActive
              ? "bg-gold-500/10 text-gold-400 border border-gold-500/18"
              : "text-muted-foreground hover:bg-ap-surface hover:text-foreground border border-transparent"
          )
        }
      >
        {({ isActive }) => (
          <>
            {/* Gold left accent bar */}
            {isActive && (
              <motion.div
                layoutId="nav-active-bar"
                className="absolute left-0 inset-y-0 w-0.5 rounded-full bg-gold-500"
                transition={{ type: "spring", stiffness: 500, damping: 35 }}
              />
            )}

            <item.icon
              className={cn(
                "w-4 h-4 flex-shrink-0 transition-colors duration-150",
                isActive
                  ? "text-gold-400"
                  : "text-muted-foreground group-hover:text-foreground"
              )}
            />

            <AnimatePresence initial={false}>
              {!collapsed && (
                <motion.span
                  initial={{ opacity: 0, width: 0 }}
                  animate={{ opacity: 1, width: "auto" }}
                  exit={{ opacity: 0, width: 0 }}
                  transition={{ duration: 0.18 }}
                  className="truncate font-medium overflow-hidden whitespace-nowrap"
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
