-- Phase 2: swell alarms
-- Run in the Supabase dashboard SQL Editor (after 003).
--
-- One row per alert. Targets a spot or buoy like favorites do. Conditions
-- are all nullable: null = ignore that condition. Set conditions must all
-- hold at the same forecast hour (see backend/alerts.py).
--
--   models:            forecast model ids the alarm watches (api ids, e.g.
--                      'gfswave025', 'mfwam', 'mop'). Empty = spot/buoy
--                      default model.
--   require_all_models false = any selected model matching triggers;
--                      true  = an hour must match in every selected model.
--   min_height_m       combined Hs at/above (m)
--   min_period_s       swell period at/above (s)
--   dir_min/dir_max    swell direction range, deg coming-from; wraps 360
--                      (e.g. 250..20 means WNW through NNE)
--   max_wind_kts       wind speed at/below (spots only; GFS is the only
--                      wind source, so no wind model selection)
--   wind_dir_min/max   wind direction range, deg coming-from; wraps 360
--   snooze_until       dedup: set to the end of the matched window after an
--                      email goes out; no re-alert for matches at or before
--                      this valid-time (a new later window re-triggers)

create table public.alarms (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references auth.users (id) on delete cascade,
    kind       text not null check (kind in ('spot', 'buoy')),
    item_id    text not null,
    enabled    boolean not null default true,

    models             text[] not null default '{}',
    require_all_models boolean not null default false,

    min_height_m numeric check (min_height_m >= 0),
    min_period_s numeric check (min_period_s >= 0),
    dir_min      numeric check (dir_min >= 0 and dir_min < 360),
    dir_max      numeric check (dir_max >= 0 and dir_max < 360),
    max_wind_kts numeric check (max_wind_kts >= 0),
    wind_dir_min numeric check (wind_dir_min >= 0 and wind_dir_min < 360),
    wind_dir_max numeric check (wind_dir_max >= 0 and wind_dir_max < 360),

    snooze_until     timestamptz,
    last_notified_at timestamptz,
    created_at       timestamptz not null default now(),

    -- at least one condition, and direction ranges come in pairs
    constraint alarms_has_condition check (
        min_height_m is not null or min_period_s is not null
        or dir_min is not null or max_wind_kts is not null
        or wind_dir_min is not null
    ),
    constraint alarms_dir_pair check ((dir_min is null) = (dir_max is null)),
    constraint alarms_wind_dir_pair check ((wind_dir_min is null) = (wind_dir_max is null))
);

create index alarms_user_idx on public.alarms (user_id);
create index alarms_enabled_idx on public.alarms (enabled) where enabled;

alter table public.alarms enable row level security;

create policy "Users can view own alarms"
    on public.alarms for select
    using (auth.uid() = user_id);

create policy "Users can create own alarms"
    on public.alarms for insert
    with check (auth.uid() = user_id);

create policy "Users can update own alarms"
    on public.alarms for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "Users can delete own alarms"
    on public.alarms for delete
    using (auth.uid() = user_id);

-- The pipeline (service role) bypasses RLS to read all alarms and write
-- snooze_until / last_notified_at after sending.
