import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { api, type ReplayRunPayload, type SetupPayload } from "@/lib/api"

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 15000,
  })
}

export function useMarket() {
  return useQuery({
    queryKey: ["market"],
    queryFn: api.market,
    refetchInterval: 10000,
  })
}

export function useMarketContext(symbol = "XAUUSD", isOnline = true) {
  return useQuery({
    queryKey: ["market-context", symbol, isOnline ? "online" : "offline"],
    queryFn: () => api.marketContext(symbol),
    refetchInterval: isOnline ? 5000 : 20000,
  })
}

export function useSignals(limit = 50) {
  return useQuery({
    queryKey: ["signals", limit],
    queryFn: () => api.signals(limit),
    refetchInterval: 30000,
  })
}

export function useTrades(status: "all" | "active" | "closed" = "all", limit = 100) {
  return useQuery({
    queryKey: ["trades", status, limit],
    queryFn: () => api.trades(status, limit),
    refetchInterval: 30000,
  })
}

export function useActiveTrades(limit = 100) {
  return useTrades("active", limit)
}

export function useAlerts(limit = 50) {
  return useQuery({
    queryKey: ["alerts", limit],
    queryFn: () => api.alerts(limit),
    refetchInterval: 20000,
  })
}

export function useAnalytics(filters?: { session?: string; confirmation_type?: string; symbol?: string }) {
  return useQuery({
    queryKey: ["analytics", filters?.session ?? "all", filters?.confirmation_type ?? "all", filters?.symbol ?? "all"],
    queryFn: () => api.analytics(filters),
    refetchInterval: 60000,
  })
}

export function useBotStatus() {
  return useQuery({
    queryKey: ["bot-status"],
    queryFn: api.bot.status,
    refetchInterval: 3000,
  })
}

export function useStartBot() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.bot.start,
    onSuccess: (data) => {
      queryClient.setQueryData(["bot-status"], data)
      queryClient.invalidateQueries({ queryKey: ["bot-status"] })
      queryClient.invalidateQueries({ queryKey: ["activity-logs"] })
    },
  })
}

export function useStopBot() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.bot.stop,
    onSuccess: (data) => {
      queryClient.setQueryData(["bot-status"], data)
      queryClient.invalidateQueries({ queryKey: ["bot-status"] })
      queryClient.invalidateQueries({ queryKey: ["activity-logs"] })
    },
  })
}

export function useRestartBot() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.bot.restart,
    onSuccess: (data) => {
      queryClient.setQueryData(["bot-status"], data)
      queryClient.invalidateQueries({ queryKey: ["bot-status"] })
      queryClient.invalidateQueries({ queryKey: ["activity-logs"] })
    },
  })
}

export function useActivityLogs(limit = 30) {
  return useQuery({
    queryKey: ["activity-logs", limit],
    queryFn: () => api.logs.activity(limit),
    refetchInterval: 5000,
  })
}

export function useRuntimeLogs(limit = 100) {
  return useQuery({
    queryKey: ["runtime-logs", limit],
    queryFn: () => api.logs.runtime(limit),
    refetchInterval: 3000,
  })
}

export function useReplayLogs(limit = 100) {
  return useQuery({
    queryKey: ["replay-logs", limit],
    queryFn: () => api.logs.replay(limit),
    refetchInterval: 5000,
  })
}

export function useTelegramLogs(limit = 100) {
  return useQuery({
    queryKey: ["telegram-logs", limit],
    queryFn: () => api.logs.telegram(limit),
    refetchInterval: 3000,
  })
}

export function useReplayLatest() {
  return useQuery({
    queryKey: ["replay-latest"],
    queryFn: api.replay.latest,
    refetchInterval: 15000,
  })
}

export function useReplayStatus(runId?: number) {
  return useQuery({
    queryKey: ["replay-status", runId ?? "none"],
    queryFn: () => api.replay.status(runId as number),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === "running" ? 2000 : false
    },
  })
}

export function useReplayResults(runId?: number) {
  return useQuery({
    queryKey: ["replay-results", runId ?? "none"],
    queryFn: () => api.replay.results(runId as number),
    enabled: Boolean(runId),
  })
}

export function useRunReplay() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: ReplayRunPayload) => api.replay.run(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["replay-latest"] })
      queryClient.invalidateQueries({ queryKey: ["bot-status"] })
      queryClient.invalidateQueries({ queryKey: ["analytics"] })
    },
  })
}

export function useSetups() {
  return useQuery({
    queryKey: ["manual-setups"],
    queryFn: api.setups.list,
    refetchInterval: 30000,
  })
}

export function useCreateSetup() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: SetupPayload) => api.setups.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["manual-setups"] })
      queryClient.invalidateQueries({ queryKey: ["alerts"] })
    },
  })
}

export function useUpdateSetup() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<SetupPayload> & { status?: string } }) =>
      api.setups.update(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["manual-setups"] })
      queryClient.invalidateQueries({ queryKey: ["alerts"] })
    },
  })
}

export function useDeleteSetup() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.setups.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["manual-setups"] })
      queryClient.invalidateQueries({ queryKey: ["alerts"] })
    },
  })
}
