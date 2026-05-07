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
        gold: {
          50: "#FFF9E3",
          100: "#FFF0B8",
          200: "#FFE98C",
          300: "#FFD766",
          400: "#F4C542",
          500: "#D9AB26",
          600: "#B58816",
          700: "#8B6710",
          800: "#664A0B",
          900: "#453006",
        },
        purple: {
          300: "#B486FF",
          400: "#8B3FF2",
          500: "#6F2DBD",
          600: "#55208E",
          700: "#3D1766",
        },
        ap: {
          bg: "#1B1020",
          surface: "#25152D",
          card: "#25152D",
          "card-hover": "#2F1B3A",
          sidebar: "#190F22",
          border: "#3B2448",
          "border-strong": "#503063",
        },
        buy: { DEFAULT: "#22C55E", dim: "rgba(34,197,94,0.12)", border: "rgba(34,197,94,0.28)" },
        sell: { DEFAULT: "#EF4444", dim: "rgba(239,68,68,0.12)", border: "rgba(239,68,68,0.28)" },
        warn: { DEFAULT: "#F4C542", dim: "rgba(244,197,66,0.12)", border: "rgba(244,197,66,0.28)" },
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
        "gold-xs": "0 0 12px rgba(244,197,66,0.12)",
        "gold-sm": "0 0 20px rgba(244,197,66,0.18)",
        "gold-md": "0 0 34px rgba(244,197,66,0.25)",
        "purple-xs": "0 0 12px rgba(111,45,189,0.15)",
        "card": "0 12px 36px rgba(12,5,16,0.42)",
        "card-hover": "0 18px 48px rgba(10,4,14,0.58)",
        "nav-active": "inset 2px 0 0 #F4C542",
      },
      backgroundImage: {
        "dot-grid": "radial-gradient(circle, rgba(80,48,99,0.48) 1px, transparent 1px)",
        "glow-gold-top": "radial-gradient(ellipse 80% 30% at 50% 0%, rgba(244,197,66,0.08) 0%, transparent 70%)",
        "glow-purple-top": "radial-gradient(ellipse 80% 36% at 50% 0%, rgba(111,45,189,0.22) 0%, transparent 72%)",
        "card-shine": "linear-gradient(135deg, rgba(255,255,255,0.05) 0%, transparent 62%)",
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
