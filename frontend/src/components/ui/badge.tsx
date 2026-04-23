import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-semibold tracking-wide transition-colors select-none",
  {
    variants: {
      variant: {
        default: "bg-ap-surface border-ap-border text-foreground",
        gold: "bg-[rgba(212,175,55,0.12)] border-[rgba(212,175,55,0.25)] text-gold-400",
        buy: "bg-buy-dim border-buy-border text-buy",
        sell: "bg-sell-dim border-sell-border text-sell",
        warn: "bg-warn-dim border-warn-border text-warn",
        muted: "bg-ap-surface border-ap-border text-muted-foreground",
        outline: "bg-transparent border-ap-border-strong text-foreground",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
