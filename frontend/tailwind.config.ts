import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Government of India palette — restrained, professional
        saffron: { 50: "#FFF5EB", 100: "#FFEAD1", 500: "#FF9933", 700: "#CC7700" },
        ink:     { 50: "#F5F7FA", 100: "#E8EDF4", 500: "#2C3E50", 700: "#1F2D3D", 900: "#0F1B2D" },
        leaf:    { 50: "#F0F7EE", 100: "#DFEBDA", 500: "#138808", 700: "#0E6606" },
        mist:    { 50: "#FAFBFC", 100: "#F0F2F5", 200: "#E1E5EA" },
        // Verdict colors
        qualified:    "#138808",
        flagged:      "#F59E0B",
        markreview:   "#2563EB",
        disqualified: "#DC2626",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        card: "0 1px 3px 0 rgb(0 0 0 / 0.08), 0 1px 2px -1px rgb(0 0 0 / 0.06)",
        elev: "0 4px 6px -1px rgb(0 0 0 / 0.06), 0 2px 4px -2px rgb(0 0 0 / 0.05)",
      },
    },
  },
  plugins: [],
};

export default config;
