-- Schéma du projet Supabase « cloach ».
-- donnees : l'état de chaque compte (profil, plan, journal), lu et écrit par l'app
--           (règles RLS : chacun ne voit que sa ligne) et par le serveur MCP (clé secrète).
-- garmin_comptes : jetons Garmin chiffrés par le serveur ; aucune règle RLS, donc
--           invisible depuis l'app : seule la clé secrète du serveur y accède.

create table if not exists public.donnees (
  user_id uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  data    jsonb not null default '{}'::jsonb,
  maj     timestamptz not null default now()
);
alter table public.donnees enable row level security;

create policy "donnees_lire" on public.donnees
  for select to authenticated using (user_id = (select auth.uid()));
create policy "donnees_ajouter" on public.donnees
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy "donnees_modifier" on public.donnees
  for update to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "donnees_supprimer" on public.donnees
  for delete to authenticated using (user_id = (select auth.uid()));

create table if not exists public.garmin_comptes (
  user_id uuid primary key references auth.users (id) on delete cascade,
  jetons  text not null,
  maj     timestamptz not null default now()
);
alter table public.garmin_comptes enable row level security;
revoke all on public.garmin_comptes from anon, authenticated;
