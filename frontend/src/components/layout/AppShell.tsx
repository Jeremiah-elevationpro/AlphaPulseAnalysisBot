import { Outlet, useLocation } from "react-router-dom"
import { AnimatePresence, motion } from "framer-motion"
import { Sidebar } from "./Sidebar"
import { TopHeader } from "./TopHeader"
import { MobileNav } from "./MobileNav"
import { SidebarContext, useSidebarState } from "@/hooks/use-sidebar"

const PAGE_VARIANTS = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -4 },
}

export function AppShell() {
  const sidebar = useSidebarState()
  const location = useLocation()

  return (
    <SidebarContext.Provider value={sidebar}>
      <div className="flex h-full overflow-hidden bg-ap-bg">
        {/* Desktop sidebar */}
        <div className="hidden lg:flex h-full">
          <Sidebar />
        </div>

        {/* Main content area */}
        <div className="flex flex-col flex-1 min-w-0 h-full overflow-hidden">
          <TopHeader />

          {/* Page content with transition */}
          <main className="relative flex-1 overflow-y-auto overflow-x-hidden pb-20 lg:pb-0">
            <div className="pointer-events-none absolute inset-0 bg-dot opacity-[0.18]" />
            <div className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-glow-purple" />
            <div className="pointer-events-none absolute inset-x-0 top-0 h-28 bg-glow-gold" />
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={location.pathname}
                variants={PAGE_VARIANTS}
                initial="initial"
                animate="animate"
                exit="exit"
                transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
                className="relative min-h-full"
              >
                <Outlet />
              </motion.div>
            </AnimatePresence>
          </main>
        </div>

        {/* Mobile bottom nav */}
        <MobileNav />
      </div>
    </SidebarContext.Provider>
  )
}
