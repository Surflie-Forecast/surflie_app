-- Phase 1: user profiles + favorite spots
-- Run this in the Supabase dashboard: SQL Editor -> paste -> Run.
--
-- Design notes:
--  * Spots live in api/spots.json (built by the pipeline), not in the DB, so
--    spot references are stored as text slugs (the stable API ids). If spots
--    ever move into the DB, these columns become FKs in a later migration.
--  * Every table gets RLS with owner-only policies: the browser talks to
--    Supabase directly with the publishable key, so RLS is the entire
--    security model.

-- ---------------------------------------------------------------------------
-- profiles: one row per auth user, auto-created on signup
-- ---------------------------------------------------------------------------
create table public.profiles (
    id           uuid primary key references auth.users (id) on delete cascade,
    email        text,
    default_spot text,                      -- spot slug, e.g. 'devereaux'
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

alter table public.profiles enable row level security;

create policy "Users can view own profile"
    on public.profiles for select
    using (auth.uid() = id);

create policy "Users can update own profile"
    on public.profiles for update
    using (auth.uid() = id)
    with check (auth.uid() = id);

-- No insert/delete policies: rows are created by the trigger below and
-- removed by the auth.users cascade.

-- Auto-create a profile whenever a user signs up (magic link included).
create function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
    insert into public.profiles (id, email)
    values (new.id, new.email)
    on conflict (id) do nothing;
    return new;
end;
$$;

create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- Keep updated_at fresh on any profile change.
create function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger profiles_touch_updated_at
    before update on public.profiles
    for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- favorites: many spots per user
-- ---------------------------------------------------------------------------
create table public.favorites (
    user_id    uuid not null references auth.users (id) on delete cascade,
    spot_slug  text not null,
    created_at timestamptz not null default now(),
    primary key (user_id, spot_slug)
);

alter table public.favorites enable row level security;

create policy "Users can view own favorites"
    on public.favorites for select
    using (auth.uid() = user_id);

create policy "Users can add own favorites"
    on public.favorites for insert
    with check (auth.uid() = user_id);

create policy "Users can remove own favorites"
    on public.favorites for delete
    using (auth.uid() = user_id);
