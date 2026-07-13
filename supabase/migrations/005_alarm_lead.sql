-- Phase 2b: alarm lead-time window
-- Run in the Supabase dashboard SQL Editor (after 004; safe to run anytime —
-- the IF NOT EXISTS makes it a no-op if applied twice).
--
-- lead_days: only alert when the matching window starts within this many
-- days from evaluation time. Null = the whole forecast horizon (~16 days).
-- Lets an alarm mean "tell me when good surf is 3 days out" instead of
-- firing on a match two weeks away that models will redraw ten times.

alter table public.alarms
    add column if not exists lead_days integer
    check (lead_days between 1 and 16);
