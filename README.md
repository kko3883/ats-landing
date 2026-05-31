# ATS — coolpaperplane.win

Automated Trading System — three-bucket strategy (Base Yield / Alpha / Convexity) executing on Interactive Brokers.

## Tech Stack

- **Frontend:** Next.js 15 + TailwindCSS 3
- **Backend:** Supabase (PostgreSQL + real-time subscriptions)
- **Hosting:** Vercel
- **Domain:** `ats.coolpaperplane.win`

## Local Development

```bash
npm install
npm run dev
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon/public key |

## Structure

```
pages/
  index.js        Landing page
  dashboard/      Live dashboard (requires auth)
components/       Reusable UI components
lib/supabase.js   Supabase client
```
