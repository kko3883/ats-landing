-- Enable realtime for ATS tables (safe to re-run)
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'signals'
  ) then
    alter publication supabase_realtime add table signals;
  end if;

  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'orders'
  ) then
    alter publication supabase_realtime add table orders;
  end if;

  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'portfolio'
  ) then
    alter publication supabase_realtime add table portfolio;
  end if;
end;
$$;
