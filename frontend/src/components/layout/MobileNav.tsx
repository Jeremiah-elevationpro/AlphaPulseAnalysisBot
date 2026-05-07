import { NavLink } from "react-router-dom"
import { LayoutDashboard, Zap, BarChart2, Bell, Settings } from "lucide-react"
import { motion } from "framer-motion"
import { cn } from "@/lib/utils"

const MOBILE_NAV = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard", end: true },
  { to: "/signals", icon: Zap, label: "Signals" },
  { to: "/trades", icon: BarChart2, label: "Trades" },
  { to: "/alerts", icon: Bell, label: "Alerts" },
  { to: "/settings", icon: Settings, label: "Settings" },
]

export function MobileNav() {
  return (
    <nav className="lg:hidden fixed bottom-0 inset-x-0 z-50 border-t border-ap-border bg-ap-sidebar/95 backdrop-blur-md pb-safe">
      <div className="flex items-center justify-around px-2 py-1">
        {MOBILE_NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className="flex-1"
          >
            {({ isActive }) => (
              <motion.div
                className="flex flex-col items-center justify-center py-2 rounded-xl transition-colors"
                whileTap={{ scale: 0.92 }}
              >
                <div
                  className={cn(
                    "w-8 h-8 rounded-lg flex items-center justify-center transition-all duration-200",
                    isActive
                      ? "bg-gold-500/15 shadow-gold-xs"
                      : "bg-transparent"
                  )}
                >
                  <item.icon
                    className={cn(
                      "w-4 h-4 transition-colors duration-200",
                      isActive ? "text-gold-400" : "text-muted-foreground"
                    )}
                  />
                </div>
                <span
                  className={cn(
                    "text-[9px] font-semibold mt-0.5 tracking-wide",
                    isActive ? "text-gold-400" : "text-muted-foreground"
                  )}
                >
                  {item.label}
                </span>
              </motion.div>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}
