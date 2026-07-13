-- Phase 1b: buoys as favorites
-- Run in the Supabase dashboard SQL Editor (after 001).
--
-- favorites generalizes from spots-only to (kind, item_id):
--   kind='spot'  item_id = spot slug   (api/spots.json)
--   kind='buoy'  item_id = CDIP id     (api/buoys.json, e.g. '203')
-- Phase-2 alarms will reference the same (kind, item_id) pair, so alerts can
-- fire on forecast conditions at a spot OR observed conditions at a buoy.
-- Existing rows keep working: spot_slug is renamed, old rows become kind='spot'.

alter table public.favorites rename column spot_slug to item_id;

alter table public.favorites
    add column kind text not null default 'spot'
    check (kind in ('spot', 'buoy'));

alter table public.favorites drop constraint favorites_pkey;
alter table public.favorites add primary key (user_id, kind, item_id);
