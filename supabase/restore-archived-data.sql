-- NEO Meme Coins - Restore All Archived Balances and Transactions
-- This script creates archive tables, soft-delete tracking, and restoration procedures
-- Target project: qziuovwcauaklgqscqys
-- Safe to paste in Supabase -> SQL Editor -> New query -> Run

begin;

-- ============================================================================
-- 1) ADD SOFT-DELETE TRACKING TO EXISTING TABLES
-- ============================================================================

-- Add archived_at column to paper_accounts if it doesn't exist
alter table public.paper_accounts
add column if not exists archived_at timestamptz,
add column if not exists deleted_at timestamptz,
add column if not exists restoration_count integer default 0;

-- Add archived_at column to paper_trades if it doesn't exist
alter table public.paper_trades
add column if not exists archived_at timestamptz,
add column if not exists deleted_at timestamptz;

-- ============================================================================
-- 2) CREATE ARCHIVE TABLES FOR HISTORICAL DATA
-- ============================================================================

-- Archive of deleted paper accounts with their final state
create table if not exists public.paper_accounts_archive (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  strategy_id text not null,
  strategy_version text not null,
  starting_balance_usd numeric(18,2) not null,
  final_balance_usd numeric(18,2) not null,
  archived_at timestamptz not null,
  deleted_at timestamptz,
  restoration_count integer default 0,
  archived_by_reason text,
  created_at timestamptz not null,
  updated_at timestamptz not null
);

-- Archive of closed/deleted paper trades
create table if not exists public.paper_trades_archive (
  id uuid primary key,
  user_id uuid not null,
  strategy_id text not null,
  strategy_version text not null,
  token_address text not null,
  token_symbol text not null,
  side text not null,
  status text not null,
  entry_price numeric,
  exit_price numeric,
  notional_usd numeric(18,2) not null,
  pnl_usd numeric(18,6),
  pnl_pct numeric(18,6),
  exit_reason text,
  opened_at timestamptz not null,
  closed_at timestamptz,
  archived_at timestamptz not null,
  deleted_at timestamptz
);

-- Create indexes on archive tables for faster queries
create index if not exists paper_accounts_archive_user_idx
  on public.paper_accounts_archive (user_id, archived_at desc);

create index if not exists paper_trades_archive_user_idx
  on public.paper_trades_archive (user_id, archived_at desc);

create index if not exists paper_trades_archive_token_idx
  on public.paper_trades_archive (token_address);

-- ============================================================================
-- 3) CREATE AUDIT LOG TABLE
-- ============================================================================

create table if not exists public.account_restore_log (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  action text not null,
  old_balance_usd numeric(18,2),
  new_balance_usd numeric(18,2),
  trades_restored integer,
  restored_at timestamptz not null default now(),
  restored_by text,
  details jsonb
);

create index if not exists restore_log_user_idx
  on public.account_restore_log (user_id, restored_at desc);

-- ============================================================================
-- 4) ENABLE ROW LEVEL SECURITY ON ARCHIVE TABLES
-- ============================================================================

alter table public.paper_accounts_archive enable row level security;
alter table public.paper_trades_archive enable row level security;
alter table public.account_restore_log enable row level security;

-- Users can read their own archived data
create policy "paper_accounts_archive_read_own"
on public.paper_accounts_archive
for select
to authenticated
using ((select auth.uid()) = user_id);

create policy "paper_trades_archive_read_own"
on public.paper_trades_archive
for select
to authenticated
using ((select auth.uid()) = user_id);

create policy "restore_log_read_own"
on public.account_restore_log
for select
to authenticated
using ((select auth.uid()) = user_id);

-- Service role has full access
grant all on public.paper_accounts_archive to service_role;
grant all on public.paper_trades_archive to service_role;
grant all on public.account_restore_log to service_role;

-- ============================================================================
-- 5) ARCHIVAL FUNCTION - Move deleted data to archive
-- ============================================================================

create or replace function public.archive_deleted_account(
  p_user_id uuid,
  p_reason text default 'Manual archival'
)
returns jsonb
language plpgsql
security definer
as $$
declare
  v_current_balance numeric(18,2);
  v_starting_balance numeric(18,2);
  v_trade_count integer;
  v_result jsonb;
begin
  -- Fetch current account state
  select balance_usd, starting_balance_usd into v_current_balance, v_starting_balance
  from public.paper_accounts
  where user_id = p_user_id;

  if v_current_balance is null then
    return jsonb_build_object('error', 'Account not found', 'user_id', p_user_id);
  end if;

  -- Archive the account
  insert into public.paper_accounts_archive (
    user_id, strategy_id, strategy_version, starting_balance_usd,
    final_balance_usd, archived_at, archived_by_reason, created_at, updated_at
  )
  select
    user_id, strategy_id, strategy_version, starting_balance_usd,
    balance_usd, now(), p_reason, created_at, updated_at
  from public.paper_accounts
  where user_id = p_user_id;

  -- Archive associated trades
  insert into public.paper_trades_archive
  select
    id, user_id, strategy_id, strategy_version, token_address, token_symbol,
    side, status, entry_price, exit_price, notional_usd, pnl_usd, pnl_pct,
    exit_reason, opened_at, closed_at, now(), null
  from public.paper_trades
  where user_id = p_user_id and status in ('CLOSED', 'CANCELLED');

  select count(*) into v_trade_count
  from public.paper_trades_archive
  where user_id = p_user_id;

  -- Mark account as archived
  update public.paper_accounts
  set
    archived_at = now(),
    updated_at = now()
  where user_id = p_user_id;

  -- Log the archival
  insert into public.account_restore_log (
    user_id, action, old_balance_usd, new_balance_usd, trades_restored, restored_by, details
  )
  values (
    p_user_id,
    'ARCHIVE',
    v_current_balance,
    null,
    v_trade_count,
    'system',
    jsonb_build_object('reason', p_reason)
  );

  v_result := jsonb_build_object(
    'status', 'archived',
    'user_id', p_user_id,
    'final_balance_usd', v_current_balance,
    'trades_archived', v_trade_count,
    'archived_at', now()
  );

  return v_result;
end;
$$;

-- ============================================================================
-- 6) RESTORATION FUNCTION - Restore archived account and trades
-- ============================================================================

create or replace function public.restore_archived_account(
  p_user_id uuid,
  p_restore_balance boolean default true,
  p_restore_trades boolean default true
)
returns jsonb
language plpgsql
security definer
as $$
declare
  v_archived_balance numeric(18,2);
  v_current_balance numeric(18,2);
  v_archived_trades integer;
  v_restored_trades integer := 0;
  v_result jsonb;
begin
  -- Get archived account data
  select final_balance_usd into v_archived_balance
  from public.paper_accounts_archive
  where user_id = p_user_id
  order by archived_at desc
  limit 1;

  if v_archived_balance is null then
    return jsonb_build_object(
      'error', 'No archived account found',
      'user_id', p_user_id
    );
  end if;

  -- Get current balance
  select balance_usd into v_current_balance
  from public.paper_accounts
  where user_id = p_user_id;

  -- Restore balance if requested
  if p_restore_balance and v_current_balance is not null then
    update public.paper_accounts
    set
      balance_usd = v_archived_balance,
      archived_at = null,
      deleted_at = null,
      restoration_count = restoration_count + 1,
      updated_at = now()
    where user_id = p_user_id;
  end if;

  -- Restore archived trades if requested
  if p_restore_trades then
    with archived_trades as (
      select * from public.paper_trades_archive
      where user_id = p_user_id and deleted_at is null
    )
    insert into public.paper_trades (
      id, user_id, strategy_id, strategy_version, token_address, token_symbol,
      side, status, entry_price, exit_price, notional_usd, pnl_usd, pnl_pct,
      exit_reason, opened_at, closed_at
    )
    select
      id, user_id, strategy_id, strategy_version, token_address, token_symbol,
      side, status, entry_price, exit_price, notional_usd, pnl_usd, pnl_pct,
      exit_reason, opened_at, closed_at
    from archived_trades
    on conflict (id) do nothing;

    select count(*) into v_restored_trades
    from public.paper_trades
    where user_id = p_user_id;
  end if;

  select count(*) into v_archived_trades
  from public.paper_trades_archive
  where user_id = p_user_id;

  -- Log the restoration
  insert into public.account_restore_log (
    user_id, action, old_balance_usd, new_balance_usd, trades_restored, restored_by, details
  )
  values (
    p_user_id,
    'RESTORE',
    v_current_balance,
    v_archived_balance,
    v_restored_trades,
    'system',
    jsonb_build_object(
      'restore_balance', p_restore_balance,
      'restore_trades', p_restore_trades
    )
  );

  v_result := jsonb_build_object(
    'status', 'restored',
    'user_id', p_user_id,
    'previous_balance_usd', v_current_balance,
    'restored_balance_usd', v_archived_balance,
    'trades_restored', v_restored_trades,
    'total_archived_trades', v_archived_trades,
    'restored_at', now()
  );

  return v_result;
end;
$$;

-- ============================================================================
-- 7) BULK RESTORATION FUNCTION - Restore all archived accounts (FIXED)
-- ============================================================================

create or replace function public.restore_all_archived_accounts()
returns table (
  user_id uuid,
  status text,
  previous_balance numeric,
  restored_balance numeric,
  trades_restored integer,
  result_message text
)
language plpgsql
security definer
as $$
declare
  v_archived_user uuid;
  v_restore_result jsonb;
  v_rec record;
begin
  -- Find all users with archived accounts
  for v_rec in
    select distinct paa.user_id
    from public.paper_accounts_archive paa
    where paa.deleted_at is null
    order by paa.archived_at desc
  loop
    v_archived_user := v_rec.user_id;

    -- Restore each archived account
    v_restore_result := public.restore_archived_account(v_archived_user, true, true);

    return query select
      v_archived_user,
      v_restore_result->>'status',
      (v_restore_result->>'old_balance_usd')::numeric,
      (v_restore_result->>'restored_balance_usd')::numeric,
      (v_restore_result->>'trades_restored')::integer,
      'Account and transactions restored successfully'::text;
  end loop;
end;
$$;

-- ============================================================================
-- 8) VERIFICATION QUERIES
-- ============================================================================

-- View archived accounts summary
create or replace view public.vw_archived_accounts as
select
  aa.user_id,
  p.username,
  aa.strategy_version,
  aa.starting_balance_usd,
  aa.final_balance_usd,
  (aa.final_balance_usd - aa.starting_balance_usd) as pnl_usd,
  ((aa.final_balance_usd - aa.starting_balance_usd) / aa.starting_balance_usd * 100) as return_pct,
  aa.archived_at,
  aa.restoration_count,
  count(pta.id) as archived_trades_count
from public.paper_accounts_archive aa
left join public.profiles p on aa.user_id = p.id
left join public.paper_trades_archive pta on aa.user_id = pta.user_id
group by aa.id, aa.user_id, p.username, aa.strategy_version,
         aa.starting_balance_usd, aa.final_balance_usd, aa.archived_at, aa.restoration_count
order by aa.archived_at desc;

-- View restoration history
create or replace view public.vw_restoration_history as
select
  arl.user_id,
  p.username,
  arl.action,
  arl.old_balance_usd,
  arl.new_balance_usd,
  (arl.new_balance_usd - arl.old_balance_usd) as balance_change,
  arl.trades_restored,
  arl.restored_at,
  arl.details
from public.account_restore_log arl
left join public.profiles p on arl.user_id = p.id
order by arl.restored_at desc;

commit;

-- ============================================================================
-- 9) RESTORATION EXECUTION
-- ============================================================================

-- Uncomment below to execute full restoration of all archived accounts:
-- select * from public.restore_all_archived_accounts();

-- Or restore individual account:
-- select public.restore_archived_account('USER_UUID_HERE'::uuid, true, true);

-- ============================================================================
-- 10) QUERY RESULTS VERIFICATION
-- ============================================================================

-- View all archived accounts and their data
select 'ARCHIVED ACCOUNTS SUMMARY' as report;
select * from public.vw_archived_accounts;

-- View restoration history
select '';
select 'RESTORATION HISTORY' as report;
select * from public.vw_restoration_history;

-- Count of data in archives
select '';
select 'ARCHIVE DATA COUNTS' as report;
select
  'paper_accounts_archive' as table_name,
  count(*) as record_count,
  min(archived_at) as earliest_archive,
  max(archived_at) as latest_archive
from public.paper_accounts_archive
union all
select
  'paper_trades_archive' as table_name,
  count(*) as record_count,
  min(archived_at) as earliest_archive,
  max(archived_at) as latest_archive
from public.paper_trades_archive;
