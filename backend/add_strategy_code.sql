-- Cloud copies of strategy folders (SimpliEarn Desk cloud backup). Run once in the Supabase
-- SQL editor as the project owner; safe to run again. Only adds two tables and grants
-- oms_app the same select/insert/update it has on the other OMS tables, plus delete on
-- strategy_files so a removed file is gone rather than kept (only the latest copy is stored).

create table if not exists public.strategy_code (
    strategy_id varchar(64) primary key references public.strategies(id),
    revision integer not null default 0,
    schedule jsonb,
    updated_by varchar(128),
    updated_at timestamptz not null default now()
);

create table if not exists public.strategy_files (
    strategy_id varchar(64) not null references public.strategies(id),
    path varchar(512) not null,
    content bytea not null,
    sha256 varchar(64) not null,
    size integer not null,
    revision integer not null,
    updated_at timestamptz not null default now(),
    primary key (strategy_id, path)
);

-- An earlier draft of this file kept removed files as rows with deleted = true.
alter table public.strategy_files drop column if exists deleted;

alter table public.strategy_code enable row level security;
alter table public.strategy_files enable row level security;
revoke all on table public.strategy_code from public, anon, authenticated, service_role;
revoke all on table public.strategy_files from public, anon, authenticated, service_role;

do $$
declare
    tbl text;
begin
    if not exists (select 1 from pg_roles where rolname = 'oms_app') then
        return;
    end if;
    foreach tbl in array array['strategy_code', 'strategy_files']
    loop
        execute format('grant select, insert, update on table public.%I to oms_app', tbl);
        execute format('drop policy if exists oms_app_access on public.%I', tbl);
        execute format(
            'create policy oms_app_access on public.%I for all to oms_app using (true) with check (true)',
            tbl
        );
    end loop;
    execute 'grant delete on table public.strategy_files to oms_app';
end $$;
