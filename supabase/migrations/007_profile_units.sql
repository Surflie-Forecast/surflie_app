-- Phase 3b: per-user unit preference
-- Run in the Supabase dashboard SQL Editor (after 006; idempotent).
--
-- Drives both the app display AND how alert emails format heights
-- (backend/alerts.py reads it). The header M/FT toggle and the account
-- panel toggle both write it; localStorage stays the anonymous fallback.

alter table public.profiles
    add column if not exists units text not null default 'imperial'
    check (units in ('metric', 'imperial'));
