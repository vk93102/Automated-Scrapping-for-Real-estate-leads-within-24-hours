# Maricopa Leads Dashboard (Next.js)

Simple black/white dashboard for Maricopa leads from Supabase.

## Setup

1. Copy envs:

```bash
cp .env.example .env.local
```

2. Fill `.env.local` values.

Use either:

- Supabase API mode: `NEXT_PUBLIC_SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` (or anon key)
- Direct DB mode: `DATABASE_URL` (Supabase Postgres connection string)

3. Install and run:

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

## Filters

- Last Day
- Last Week
- Last Month
- End to End (all records)

## Current scope

Shows only Maricopa leads by reading from:

- `properties`
- linked `documents`
