-- Phase 4b: let users be deleted without violating invite_codes_used_pair
-- Run in the Supabase dashboard SQL Editor (after 009). Idempotent.
--
-- Bug found 2026-07-21: deleting a user failed outright with "Database error
-- deleting user". invite_codes.used_by is ON DELETE SET NULL, but the table
-- also carries
--     check ((used_by is null) = (used_at is null))
-- so the cascade nulled used_by, left used_at set, and the constraint aborted
-- the whole delete. Same for created_by, though nothing constrains that one.
--
-- Fix: a BEFORE DELETE trigger on auth.users releases the user's codes first,
-- clearing used_by and used_at together. A code whose redeemer is deleted
-- returns to the unclaimed pool, which is the behaviour we want anyway —
-- otherwise the code is burned forever on an account that no longer exists.

create function public.release_invite_codes_on_user_delete()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
    -- Both columns together, so the used_pair check always holds.
    update public.invite_codes
       set used_by = null, used_at = null
     where used_by = old.id;

    -- Codes this user minted survive as unowned (created_by is already
    -- ON DELETE SET NULL, and nothing constrains it) — they stay valid for
    -- whoever holds them.
    return old;
end;
$$;

drop trigger if exists on_auth_user_deleted_release_codes on auth.users;

create trigger on_auth_user_deleted_release_codes
    before delete on auth.users
    for each row execute function public.release_invite_codes_on_user_delete();

-- Repair any row already left inconsistent by a partially-applied delete.
update public.invite_codes
   set used_at = null
 where used_by is null and used_at is not null;
