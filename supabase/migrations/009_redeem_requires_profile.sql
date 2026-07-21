-- Phase 4a: don't consume a code when there's no profile to activate
-- Run in the Supabase dashboard SQL Editor (after 008). Idempotent.
--
-- Bug found 2026-07-21: redeem_invite_code() claimed the code, then ran
--   update public.profiles set activated = true where id = v_uid
-- without checking that it matched anything. An auth.users row whose profiles
-- row is missing (delete the profile but not the user, and handle_new_user()
-- never fires again — it's an AFTER INSERT trigger on auth.users) therefore
-- burned a code and stayed unactivated, with no error shown. The account was
-- unrecoverable from the UI and the code was gone.
--
-- Two guards:
--   * check the profile exists BEFORE touching invite_codes, so a broken
--     account can't consume anything
--   * verify the activation UPDATE actually matched a row, so any future
--     silent no-op raises instead of half-completing

create or replace function public.redeem_invite_code(p_code text)
returns boolean
language plpgsql
security definer set search_path = ''
as $$
declare
    v_uid       uuid := auth.uid();
    v_activated boolean;
    v_claimed   integer;
begin
    if v_uid is null then
        raise exception 'Not signed in';
    end if;

    -- Note the three-way distinction: true = already in, false = needs a
    -- code, null = no profile row at all (the broken state above).
    select activated into v_activated from public.profiles where id = v_uid;

    if v_activated is null then
        raise exception 'No profile for this account — contact support';
    end if;

    if v_activated then
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

    get diagnostics v_claimed = row_count;

    if v_claimed = 0 then
        -- Unreachable given the check above, but this is the failure that
        -- silently ate a code once. Raising rolls the claim back with it.
        raise exception 'Could not activate this account';
    end if;

    -- Mint this user's own codes so the tree can grow one step further.
    perform public.mint_invite_codes();

    return true;
end;
$$;

revoke all on function public.redeem_invite_code(text) from public;
grant execute on function public.redeem_invite_code(text) to authenticated;

-- ---------------------------------------------------------------------------
-- Release codes stranded by a deleted user
-- ---------------------------------------------------------------------------
-- invite_codes.used_by is ON DELETE SET NULL, so deleting a user leaves
-- used_by null with used_at still set — which violates invite_codes_used_pair
-- and leaves the code in an ambiguous state. Reset those rows to fully
-- unclaimed so they can be handed out again.
update public.invite_codes
   set used_at = null
 where used_by is null and used_at is not null;

-- ---------------------------------------------------------------------------
-- Deleting a test account
-- ---------------------------------------------------------------------------
-- Delete from auth.users (dashboard: Authentication -> Users), NOT from
-- profiles. The profiles row cascades from the user; deleting it on its own
-- strands the account, since handle_new_user() only fires on INSERT into
-- auth.users and a returning OAuth sign-in creates no new user row.
--
-- To repair an account already in that state:
--   insert into public.profiles (id, email, activated, invites_remaining)
--   select id, email, true, 1 from auth.users where email = '<email>'
--   on conflict (id) do update set activated = true;
