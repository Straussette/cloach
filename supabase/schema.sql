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

-- activites : historique des activités (importé depuis Garmin par le serveur),
--           une ligne par activité ; lu par l'app (onglet Stats) et par Claude.
create table if not exists public.activites (
  user_id      uuid not null references auth.users (id) on delete cascade,
  id           text not null,               -- identifiant chez la source (activityId Garmin)
  source       text not null default 'garmin',
  jour         date not null,               -- date locale de début
  debut        text,                        -- date et heure locales, AAAA-MM-JJ HH:MM:SS
  sport        text not null,               -- course, trail, velo, renfo, natation, autre
  type         text,                        -- type détaillé chez la source (ex. virtual_ride)
  nom          text,
  distance_km  numeric,
  duree_min    numeric,
  denivele_m   numeric,
  fc_moy       numeric,
  fc_max       numeric,
  charge       numeric,                     -- charge d'entraînement Garmin
  te_aerobie   numeric,
  te_anaerobie numeric,
  puissance_moy  numeric,
  puissance_norm numeric,
  calories     numeric,
  primary key (user_id, id)
);
create index if not exists activites_jour on public.activites (user_id, jour);
alter table public.activites enable row level security;

create policy "activites_lire" on public.activites
  for select to authenticated using (user_id = (select auth.uid()));
create policy "activites_supprimer" on public.activites
  for delete to authenticated using (user_id = (select auth.uid()));

-- strava_comptes : jetons Strava chiffrés par le serveur, comme garmin_comptes (aucune règle RLS).
create table if not exists public.strava_comptes (
  user_id uuid primary key references auth.users (id) on delete cascade,
  jetons  text not null,
  maj     timestamptz not null default now()
);
alter table public.strava_comptes enable row level security;
revoke all on public.strava_comptes from anon, authenticated;
