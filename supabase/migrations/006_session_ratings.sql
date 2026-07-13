-- Phase 3: session ratings — the ML training-data seed
-- Run in the Supabase dashboard SQL Editor (after 005).
--
-- One row per surfed session. The point is the pairing: a subjective rating
-- JOINED to a frozen snapshot of the conditions that produced it, captured
-- at write time (past hourly files get pruned and model runs overwritten,
-- so conditions are unrecoverable later). Future model training reads:
-- "models said X, buoy measured Y, surfer felt Z".
--
--   rating      0.5..5.0 in half-star steps
--   session_at  the session moment (client sends the chosen hour; today-only
--               in the UI for now — the timestamp already supports any past
--               moment once a conditions archive exists to snapshot from)
--   conditions  jsonb snapshot, client-built at save time:
--                 forecast: hourly swell/wind (+ score) at that hour
--                 tide:     height nearest that hour
--                 buoy:     closest buoy's OBSERVED hs/tp/dp nearest that
--                           hour (ground truth, ~3-day window)
--               Deliberately schemaless: sources will evolve; training code
--               reads jsonb. Missing pieces are simply absent keys.

create table public.session_ratings (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references auth.users (id) on delete cascade,
    spot_slug  text not null,
    session_at timestamptz not null,
    rating     numeric not null check (
        rating between 0.5 and 5 and rating * 2 = round(rating * 2)),
    note       text,
    conditions jsonb,
    created_at timestamptz not null default now()
);

create index session_ratings_user_spot_idx
    on public.session_ratings (user_id, spot_slug, session_at desc);

alter table public.session_ratings enable row level security;

create policy "Users can view own sessions"
    on public.session_ratings for select
    using (auth.uid() = user_id);

create policy "Users can log own sessions"
    on public.session_ratings for insert
    with check (auth.uid() = user_id);

create policy "Users can update own sessions"
    on public.session_ratings for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "Users can delete own sessions"
    on public.session_ratings for delete
    using (auth.uid() = user_id);
