-- Which laptops have each strategy set up in SimpliEarn Desk (Settings tab on a strategy).
-- Run once in the Supabase SQL editor as the project owner; safe to run again. Adds one table
-- and grants oms_app select/insert/update on it, like the other OMS tables (no delete: a
-- strategy removed from a laptop is kept as installed = false).

create table if not exists public.strategy_installs (
    strategy_id varchar(64) not null references public.strategies(id),
    host varchar(128) not null,
    installed boolean not null default true,
    version varchar(64),
    schedule jsonb,
    last_run_at timestamptz,
    last_run_status varchar(32),
    seen_at timestamptz not null default now(),
    primary key (strategy_id, host)
);

alter table public.strategy_installs enable row level security;
revoke all on table public.strategy_installs from public, anon, authenticated, service_role;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'oms_app') then
        return;
    end if;
    execute 'grant select, insert, update on table public.strategy_installs to oms_app';
    execute 'drop policy if exists oms_app_access on public.strategy_installs';
    execute 'create policy oms_app_access on public.strategy_installs for all to oms_app using (true) with check (true)';
end $$;
