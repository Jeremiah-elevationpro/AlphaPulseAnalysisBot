import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-40 select-none",
  {
    variants: {
      variant: {
        default:
          "bg-gold-500 text-ap-bg hover:bg-gold-400 shadow shadow-gold-xs font-semibold",
        outline:
          "border border-ap-border-strong bg-transparent text-foreground hover:bg-ap-surface hover:border-ap-border-strong/80",
        ghost:
          "text-muted-foreground hover:bg-ap-surface hover:text-foreground",
        secondary:
          "bg-ap-surface text-foreground border border-ap-border hover:bg-ap-card-hover",
        buy: "bg-buy-dim text-buy border border-buy-border hover:bg-[rgba(16,185,129,0.2)] font-semibold",
        sell: "bg-sell-dim text-sell border border-sell-border hover:bg-[rgba(239,68,68,0.2)] font-semibold",
        destructive:
          "bg-destructive/10 text-destructive border border-destructive/25 hover:bg-destructive/20",
        link: "text-gold-400 underline-offset-4 hover:underline p-0 h-auto",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 px-3 text-xs rounded-md",
        lg: "h-10 px-6 rounded-lg",
        xl: "h-11 px-8 text-base rounded-lg",
        icon: "h-9 w-9",
        "icon-sm": "h-7 w-7 rounded-md",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
