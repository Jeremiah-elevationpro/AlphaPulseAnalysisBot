import { createContext, useContext, useState, useCallback } from "react"

interface SidebarState {
  collapsed: boolean
  toggle: () => void
  setCollapsed: (v: boolean) => void
}

export const SidebarContext = createContext<SidebarState>({
  collapsed: false,
  toggle: () => {},
  setCollapsed: () => {},
})

export function useSidebarState(): SidebarState {
  const [collapsed, setCollapsed] = useState(false)
  const toggle = useCallback(() => setCollapsed((c) => !c), [])
  return { collapsed, toggle, setCollapsed }
}

export function useSidebar() {
  return useContext(SidebarContext)
}
