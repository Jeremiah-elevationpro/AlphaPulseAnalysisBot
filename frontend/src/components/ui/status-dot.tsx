import { cn } from "@/lib/utils"

interface StatusDotProps {
  status: "online" | "offline" | "warning" | "inactive"
  size?: "xs" | "sm" | "md"
  pulse?: boolean
  className?: string
}

const sizeMap = {
  xs: "w-1.5 h-1.5",
  sm: "w-2 h-2",
  md: "w-2.5 h-2.5",
}

const colorMap = {
  online: "bg-buy",
  offline: "bg-sell",
  warning: "bg-warn",
  inactive: "bg-muted-foreground",
}

export function StatusDot({ status, size = "sm", pulse = false, className }: StatusDotProps) {
  return (
    <span className={cn("relative inline-flex items-center justify-center", className)}>
      <span
        className={cn(
          "rounded-full flex-shrink-0",
          sizeMap[size],
          colorMap[status]
        )}
      />
      {pulse && status === "online" && (
        <span
          className={cn(
            "absolute inline-flex rounded-full opacity-60 animate-pulse-ring",
            sizeMap[size],
            colorMap[status]
          )}
        />
      )}
    </span>
  )
}
