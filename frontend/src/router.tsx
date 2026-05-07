import { createBrowserRouter } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import Dashboard from "@/pages/Dashboard"
import Signals from "@/pages/Signals"
import ManualSetups from "@/pages/ManualSetups"
import Trades from "@/pages/Trades"
import Alerts from "@/pages/Alerts"
import Analytics from "@/pages/Analytics"
import Settings from "@/pages/Settings"

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "signals", element: <Signals /> },
      { path: "setups", element: <ManualSetups /> },
      { path: "trades", element: <Trades /> },
      { path: "alerts", element: <Alerts /> },
      { path: "analytics", element: <Analytics /> },
      { path: "settings", element: <Settings /> },
    ],
  },
])
