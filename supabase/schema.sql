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

-- Analyse par terrain de chaque sortie de course ou de trail (plat, montée, descente, montées), calculée
-- par le serveur avec la VAP (serveur/terrains.py) : { v, temps_s, plat, montee, descente, montees, efforts }.
alter table public.activites add column if not exists terrains jsonb;
-- Meilleure puissance moyenne par durée (secondes → W) donnée par Garmin dans le résumé (maxAvgPower_N).
alter table public.activites add column if not exists puissances_max jsonb;

-- strava_comptes : jetons Strava chiffrés par le serveur, comme garmin_comptes (aucune règle RLS).
create table if not exists public.strava_comptes (
  user_id uuid primary key references auth.users (id) on delete cascade,
  jetons  text not null,
  maj     timestamptz not null default now()
);
alter table public.strava_comptes enable row level security;
revoke all on public.strava_comptes from anon, authenticated;

-- retours : messages « Donner mon avis » des testeurs. L'app peut en ajouter mais pas les relire ;
--           on les lit dans le tableau de bord Supabase (jointure sur auth.users pour l'e-mail).
create table if not exists public.retours (
  id         bigint generated always as identity primary key,
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  texte      text not null check (char_length(texte) between 1 and 4000),
  version    text check (char_length(version) <= 40),
  appareil   text check (char_length(appareil) <= 300),
  cree       timestamptz not null default now()
);
alter table public.retours enable row level security;
revoke all on public.retours from anon;
revoke update, delete, truncate on public.retours from authenticated;
create policy "retours_ajouter" on public.retours
  for insert to authenticated with check (user_id = (select auth.uid()));

-- Suppression d'un compte (route /compte/supprimer du serveur) : toutes les tables ci-dessus
-- référencent auth.users avec « on delete cascade », tout part avec le compte.

-- admins : comptes qui ont accès aux options réservées (analyse de tout l'historique en tâche de fond).
-- Aucune règle RLS : seul le serveur (clé secrète) la lit.
create table if not exists public.admins (
  user_id uuid primary key references auth.users (id) on delete cascade,
  cree    timestamptz not null default now()
);
alter table public.admins enable row level security;
revoke all on public.admins from anon, authenticated;

-- ---------- Historique des documents ----------
-- Filet de sécurité : les 30 dernières versions du document de chaque compte (copie de l'ancienne version
-- à chaque modification), pour pouvoir restaurer après une écriture malheureuse. Lue par le serveur seul
-- (RLS sans règle), effacée avec le compte.
create table if not exists public.donnees_historique (
  id bigserial primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  data jsonb not null,
  archive timestamptz not null default now()
);
create index if not exists donnees_historique_user on public.donnees_historique (user_id, archive desc);
alter table public.donnees_historique enable row level security;

create or replace function public.archiver_donnees() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if old.data is distinct from new.data then
    insert into public.donnees_historique (user_id, data) values (old.user_id, old.data);
    delete from public.donnees_historique h
     where h.user_id = old.user_id
       and h.id not in (select id from public.donnees_historique where user_id = old.user_id order by archive desc, id desc limit 30);
  end if;
  return new;
end $$;
revoke all on function public.archiver_donnees() from public, anon, authenticated;

drop trigger if exists archiver_donnees on public.donnees;
create trigger archiver_donnees before update on public.donnees
  for each row execute function public.archiver_donnees();
