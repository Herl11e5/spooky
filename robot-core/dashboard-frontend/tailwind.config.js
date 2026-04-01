/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'spooky-dark': '#0d0d0d',
        'spooky-neon-green': '#39ff14',
        'spooky-neon-cyan': '#00c8ff',
        'spooky-neon-purple': '#c77dff',
        'spooky-neon-yellow': '#ffd60a',
        'spooky-neon-red': '#ff4444',
        'spooky-neon-pink': '#ff69b4',
      },
      animation: {
        'pulse-neon': 'pulse-neon 2s ease-in-out infinite',
        'antenna-bounce': 'antenna-bounce 2s ease-in-out infinite',
      },
      keyframes: {
        'pulse-neon': {
          '0%, 100%': { opacity: '0.5' },
          '50%': { opacity: '1' },
        },
        'antenna-bounce': {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-5px)' },
        },
      },
    },
  },
}

