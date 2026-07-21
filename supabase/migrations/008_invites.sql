-- Phase 4: invite-only access
-- Run in the Supabase dashboard SQL Editor (after 007).
--
-- Growth gate: anyone can browse the app and authenticate (magic link,
-- password, Google — whatever providers are enabled), but an account can't
-- write anything until it redeems an invite code. Gating ACCESS rather than
-- SIGNUP is deliberate: OAuth providers bypass Supabase's "enable signups"
-- toggle, so a signup-side gate would leak the moment Google is turned on.
-- Enforcement therefore lives in RLS, downstream of authentication, and is
-- identical for every provider.
--
-- Consequence: an uninvited visitor CAN create an account. They just land
-- with activated=false and every write is refused until they enter a code.
-- The client shows an "enter your invite code" prompt in that state.
--
-- Seeding: this migration creates no codes. Mint the first ones by hand
-- (see the bottom of this file), hand them out, and the tree grows from
-- there — each activated user gets invites_remaining codes of their own.
--
-- This file is idempotent: the teardown block below drops everything it
-- creates, so it can be re-run from scratch during development.

-- ---------------------------------------------------------------------------
-- Teardown (safe on a fresh database — everything is IF EXISTS)
-- ---------------------------------------------------------------------------
-- NOTE: activated / invites_remaining are COLUMNS on profiles, not a table.
-- Dropping invite_codes alone would leave them behind, still holding stale
-- state, so they go here too. This discards all issued codes.
drop trigger if exists profiles_freeze_invite_columns on public.profiles;
drop function if exists public.freeze_invite_columns();
drop function if exists public.redeem_invite_code(text);
drop function if exists public.mint_invite_codes();
drop function if exists public.get_invite_codes();
drop table if exists public.invite_codes;
alter table public.profiles drop column if exists activated;
alter table public.profiles drop column if exists invites_remaining;

-- ---------------------------------------------------------------------------
-- profiles: activation state + per-user invite budget
-- ---------------------------------------------------------------------------
-- activated         false until a code is redeemed; the RLS gate reads this
-- invites_remaining how many codes this user may still mint. Spent down to 0
--                   at redemption. To grant more later, mint directly — see
--                   the note at the bottom of this file.
alter table public.profiles
    add column activated boolean not null default false;

alter table public.profiles
    add column invites_remaining integer not null default 1
    check (invites_remaining >= 0);

-- Existing users predate the gate — grandfather them in rather than locking
-- out the accounts that already have favorites and alarms.
update public.profiles set activated = true where activated = false;

-- ---------------------------------------------------------------------------
-- invite_codes
-- ---------------------------------------------------------------------------
-- One row per code. used_by null = unclaimed. created_by null = minted by
-- hand (the seed codes); otherwise the user who spent an invite to mint it.
create table public.invite_codes (
    code       text primary key,
    created_by uuid references auth.users (id) on delete set null,
    used_by    uuid references auth.users (id) on delete set null,
    used_at    timestamptz,
    created_at timestamptz not null default now(),

    constraint invite_codes_used_pair check ((used_by is null) = (used_at is null))
);

create index invite_codes_created_by_idx on public.invite_codes (created_by);

-- A user redeems at most one code, so a claimed code can't be spread around
-- by re-running the redeem call with a second account.
create unique index invite_codes_used_by_key
    on public.invite_codes (used_by) where used_by is not null;

alter table public.invite_codes enable row level security;

-- Users see the codes they minted (to display/share them) and the one they
-- redeemed. Unclaimed codes minted by someone else stay invisible —
-- otherwise anyone with an account could read the pool and self-activate.
create policy "Users can view own invite codes"
    on public.invite_codes for select
    using (auth.uid() = created_by or auth.uid() = used_by);

-- No insert/update/delete policies: codes are minted and claimed only by the
-- security definer functions below, which enforce the budget and the
-- single-use claim. Direct writes from the browser are refused.

-- ---------------------------------------------------------------------------
-- get_invite_codes: the read path
-- ---------------------------------------------------------------------------
-- What the client calls on load. SELECT ONLY — it must never mint. Minting on
-- read is what made an earlier version of this migration issue a fresh code
-- on every login: the client calls this more than once per session (supabase
-- fires several auth events for one login), so a read that mints is a read
-- that over-mints. Codes are created exactly once, at redemption.
create function public.get_invite_codes()
returns setof text
language sql
stable
security definer set search_path = ''
as $$
    select code from public.invite_codes
     where created_by = auth.uid() and used_by is null
     order by created_at;
$$;

revoke all on function public.get_invite_codes() from public;
grant execute on function public.get_invite_codes() to authenticated;

-- ---------------------------------------------------------------------------
-- mint_invite_codes: spend the caller's budget to create codes
-- ---------------------------------------------------------------------------
-- Internal only — NOT granted to authenticated. redeem_invite_code() is the
-- sole caller, so the budget is spent exactly once per account.
create function public.mint_invite_codes()
returns void
language plpgsql
security definer set search_path = ''
as $$
declare
    v_uid  uuid := auth.uid();
    v_left integer;
    v_code text;
begin
    if v_uid is null then
        raise exception 'Not signed in';
    end if;

    -- Let this transaction's profile writes through the freeze trigger.
    perform set_config('app.invite_admin', 'on', true);

    select invites_remaining into v_left from public.profiles
     where id = v_uid for update;

    while v_left > 0 loop
        -- 8 chars from a base32-ish alphabet, ambiguous glyphs (0/O, 1/I)
        -- removed so codes survive being read aloud or copied by hand.
        v_code := (
            select string_agg(
                substr('ABCDEFGHJKLMNPQRSTUVWXYZ23456789',
                       floor(random() * 32)::integer + 1, 1), '')
              from generate_series(1, 8));
        begin
            insert into public.invite_codes (code, created_by)
            values (v_code, v_uid);
        exception when unique_violation then
            continue;                     -- astronomically rare; just retry
        end;
        v_left := v_left - 1;
    end loop;

    update public.profiles set invites_remaining = 0 where id = v_uid;
end;
$$;

revoke all on function public.mint_invite_codes() from public;

-- ---------------------------------------------------------------------------
-- redeem_invite_code: claim a code and activate the caller
-- ---------------------------------------------------------------------------
-- Called by the client when profiles.activated is false. Returns true on
-- success; raises a readable exception otherwise so the UI can show why.
--
-- The claim is a single atomic UPDATE guarded by "used_by is null", so two
-- people racing the same code can't both win — the loser's update matches
-- zero rows.
create function public.redeem_invite_code(p_code text)
returns boolean
language plpgsql
security definer set search_path = ''
as $$
declare
    v_uid     uuid := auth.uid();
    v_claimed integer;
begin
    if v_uid is null then
        raise exception 'Not signed in';
    end if;

    if (select activated from public.profiles where id = v_uid) then
        return true;                      -- already in; treat as a no-op
    end if;

    perform set_config('app.invite_admin', 'on', true);

    -- Normalize: codes are handed out verbatim but typed by humans.
    p_code := upper(trim(p_code));

    update public.invite_codes
       set used_by = v_uid, used_at = now()
     where code = p_code and used_by is null;

    get diagnostics v_claimed = row_count;

    if v_claimed = 0 then
        raise exception 'That invite code is not valid or has already been used';
    end if;

    update public.profiles set activated = true where id = v_uid;

    -- Mint this user's own codes so the tree can grow one step further.
    perform public.mint_invite_codes();

    return true;
end;
$$;

revoke all on function public.redeem_invite_code(text) from public;
grant execute on function public.redeem_invite_code(text) to authenticated;

-- ---------------------------------------------------------------------------
-- The gate: activated accounts only, on every user-writable table
-- ---------------------------------------------------------------------------
-- SELECT policies are left alone — an unactivated user reading their own
-- (empty) rows is harmless, and it keeps the app's load path working before
-- the code is entered. Only writes are gated.
create or replace function public.is_activated()
returns boolean
language sql
stable
security definer set search_path = ''
as $$
    select coalesce((select activated from public.profiles
                      where id = auth.uid()), false);
$$;

grant execute on function public.is_activated() to authenticated;

drop policy if exists "Users can add own favorites" on public.favorites;
create policy "Users can add own favorites"
    on public.favorites for insert
    with check (auth.uid() = user_id and public.is_activated());

drop policy if exists "Users can create own alarms" on public.alarms;
create policy "Users can create own alarms"
    on public.alarms for insert
    with check (auth.uid() = user_id and public.is_activated());

drop policy if exists "Users can update own alarms" on public.alarms;
create policy "Users can update own alarms"
    on public.alarms for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id and public.is_activated());

drop policy if exists "Users can log own sessions" on public.session_ratings;
create policy "Users can log own sessions"
    on public.session_ratings for insert
    with check (auth.uid() = user_id and public.is_activated());

drop policy if exists "Users can update own sessions" on public.session_ratings;
create policy "Users can update own sessions"
    on public.session_ratings for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id and public.is_activated());

-- profiles stays ungated for update: an unactivated user still needs to set
-- units and a default spot, and those are self-scoped by RLS anyway. Note
-- that activated/invites_remaining are therefore writable by the owner —
-- see the trigger below, which pins them.

-- ---------------------------------------------------------------------------
-- Stop users granting themselves access
-- ---------------------------------------------------------------------------
-- The profiles UPDATE policy lets a user write their own row, which would
-- otherwise let them PATCH activated=true or invites_remaining=999 straight
-- from the browser. Freeze both columns against ordinary writes.
--
-- SECURITY DEFINER does NOT clear auth.uid() — the JWT claim persists through
-- the call — so the functions above cannot be recognised by uid alone. They
-- announce themselves with a transaction-local flag instead; set_config's
-- is_local=true means it cannot leak to a later statement on a pooled
-- connection, and the browser has no way to set it (PostgREST refuses
-- arbitrary SET, and no granted function does it on the caller's behalf).
create function public.freeze_invite_columns()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
    if auth.uid() is not null
       and coalesce(current_setting('app.invite_admin', true), '') <> 'on' then
        new.activated         := old.activated;
        new.invites_remaining := old.invites_remaining;
    end if;
    return new;
end;
$$;

create trigger profiles_freeze_invite_columns
    before update on public.profiles
    for each row execute function public.freeze_invite_columns();

-- ---------------------------------------------------------------------------
-- Seeding the first codes
-- ---------------------------------------------------------------------------
-- Run this SEPARATELY (not part of the migration — re-running the migration
-- would otherwise mint duplicates). created_by null = minted by you, not
-- spent from anyone's budget. Change the 10 to your first-wave size.
--
-- insert into public.invite_codes (code)
-- select string_agg(
--            substr('ABCDEFGHJKLMNPQRSTUVWXYZ23456789',
--                   floor(random() * 32)::integer + 1, 1), '')
--   from generate_series(1, 8)
--  cross join generate_series(1, 10) g
--  group by g;
--
-- Then read them back:
--   select code from public.invite_codes
--    where created_by is null and used_by is null;
--
-- ---------------------------------------------------------------------------
-- Granting someone more invites later
-- ---------------------------------------------------------------------------
-- Bumping invites_remaining in the dashboard does NOT mint anything (the read
-- path is select-only by design). Mint directly for that user instead:
--
-- insert into public.invite_codes (code, created_by)
-- select string_agg(
--            substr('ABCDEFGHJKLMNPQRSTUVWXYZ23456789',
--                   floor(random() * 32)::integer + 1, 1), ''),
--        '<user-uuid>'::uuid
--   from generate_series(1, 8)
--  cross join generate_series(1, 3) g    -- 3 = how many codes
--  group by g;
