-- Phase 1c: allow a buoy as the default landing view
-- Run in the Supabase dashboard SQL Editor (after 002).
--
-- profiles.default_spot generalizes to (default_kind, default_item), same
-- shape as favorites: kind='spot' + slug, or kind='buoy' + CDIP id.
-- An existing default spot is preserved (rename + kind defaults to 'spot').

alter table public.profiles rename column default_spot to default_item;

alter table public.profiles
    add column default_kind text not null default 'spot'
    check (default_kind in ('spot', 'buoy'));
