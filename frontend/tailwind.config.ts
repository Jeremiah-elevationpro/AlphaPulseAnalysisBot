import type { Config } from "tailwindcss"

const config: Config = {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        // AlphaPulse brand palette
        gold: {
          50: "#FFFBEB",
          100: "#FEF3C7",
          200: "#FDE68A",
          300: "#FCD34D",
          400: "#F0C040",
          500: "#D4AF37",
          600: "#B8941A",
          700: "#92700F",
          800: "#6B4F07",
          900: "#4A3605",
        },
        ap: {
          bg: "#07080E",
          surface: "#0C0F1A",
          card: "#111520",
          "card-hover": "#161B28",
          sidebar: "#080A14",
          border: "#1A1F30",
          "border-strong": "#252B40",
        },
        buy: { DEFAULT: "#10B981", dim: "rgba(16,185,129,0.12)", border: "rgba(16,185,129,0.25)" },
        sell: { DEFAULT: "#EF4444", dim: "rgba(239,68,68,0.12)", border: "rgba(239,68,68,0.25)" },
        warn: { DEFAULT: "#F59E0B", dim: "rgba(245,158,11,0.12)", border: "rgba(245,158,11,0.25)" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "Consolas", "monospace"],
      },
      fontSize: {
        "2xs": ["0.625rem", { lineHeight: "0.875rem" }],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      boxShadow: {
        "gold-xs": "0 0 8px rgba(212,175,55,0.10)",
        "gold-sm": "0 0 16px rgba(212,175,55,0.15)",
        "gold-md": "0 0 28px rgba(212,175,55,0.22)",
        "card": "0 2px 16px rgba(0,0,0,0.45)",
        "card-hover": "0 6px 32px rgba(0,0,0,0.65)",
        "nav-active": "inset 2px 0 0 #D4AF37",
      },
      backgroundImage: {
        "dot-grid": "radial-gradient(circle, rgba(26,31,48,0.7) 1px, transparent 1px)",
        "glow-gold-top": "radial-gradient(ellipse 80% 30% at 50% 0%, rgba(212,175,55,0.07) 0%, transparent 70%)",
        "card-shine": "linear-gradient(135deg, rgba(255,255,255,0.04) 0%, transparent 60%)",
      },
      backgroundSize: {
        "dot-28": "28px 28px",
      },
      keyframes: {
        "pulse-ring": {
          "0%": { transform: "scale(1)", opacity: "0.8" },
          "100%": { transform: "scale(2.4)", opacity: "0" },
        },
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-right": {
          "0%": { opacity: "0", transform: "translateX(-16px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        "shimmer": {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        "ticker": {
          "0%": { transform: "translateX(0)" },
          "100%": { transform: "translateX(-50%)" },
        },
      },
      animation: {
        "pulse-ring": "pulse-ring 2s ease-out infinite",
        "fade-up": "fade-up 0.4s ease-out both",
        "fade-in": "fade-in 0.3s ease-out both",
        "slide-right": "slide-right 0.3s ease-out both",
        "shimmer": "shimmer 2s linear infinite",
        "ticker": "ticker 40s linear infinite",
      },
    },
  },
  plugins: [],
}

export default config
