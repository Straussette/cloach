// Vérification de l'app dans un vrai navigateur (Playwright + Chromium), sans réseau :
// Supabase et le serveur sont simulés, l'état du compte est riche (plan, cycle, calendrier, mémoire, avis…).
// On parcourt les quatre onglets, on ouvre les cartes repliables et on vérifie :
//  - aucune erreur JavaScript ni console.error ;
//  - chaque bouton ou lien qui ouvre Claude a bien une adresse (le bug du 27/11 : liens morts) ;
//  - les éléments principaux de chaque onglet sont affichés ;
//  - pas de défilement horizontal sur un écran de téléphone, en clair comme en sombre.
// Lancement : node tests/app.test.js (ou tests/verifier.sh, qui fait tout).
const path = require('path');
const http = require('http');
const fs = require('fs');
const { chromium } = require('playwright');

const RACINE = path.join(__dirname, '..', 'docs');
const jour = (d) => { const x = new Date(); x.setDate(x.getDate() + d); return x.toISOString().slice(0, 10); };
const lundi = (() => { const x = new Date(); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x.toISOString().slice(0, 10); })();
const decale = (j, d) => { const x = new Date(j + 'T12:00:00'); x.setDate(x.getDate() + d); return x.toISOString().slice(0, 10); };

// Activités : une sortie par jour ou presque sur 8 semaines, plusieurs sports
const ACTIVITES = [];
for (let i = 0; i < 56; i++) {
  if (i % 7 === 3) continue;
  const sport = ['course', 'trail', 'velo', 'course', 'renfo', 'course', 'velo'][i % 7];
  ACTIVITES.push({
    id: String(1000 + i), jour: jour(-i), debut: `${jour(-i)}T07:00:00`, sport, type: sport === 'velo' ? 'road_biking' : 'running',
    nom: `Sortie ${i}`, distance_km: sport === 'renfo' ? 0 : 8 + (i % 5), duree_min: 40 + (i % 6) * 10, denivele_m: sport === 'trail' ? 600 : 80,
    charge: 60 + i, fc_moy: 140, puissance_moy: 220, tv: 7,
  });
}
const seance = (id, j, sport, titre, extra = {}) => ({ id, jour: j, sport, titre, objectif: `But de ${titre}`, duree: 45, intensite: 'facile', details: 'Détails.', ...extra });
const ETAT = {
  maj: Date.now(),
  profil: { prenom: 'Test', niveau: 'intermediaire', objectif: 'Trail 42 km', dateObjectif: jour(70), seancesSemaine: 4, ftp: 250, poids: 70, sportPrioritaire: 'trail' },
  garmin: { connecte: true, nom: 'Test', dernierImport: jour(0) },
  journal: [{ id: 'j1', date: jour(-2), sport: 'natation', duree: 30, ressenti: 3, notes: 'Piscine' }],
  commentaires: { 1000: { ressenti: 4, notes: 'Bien', date: jour(0) } },
  plan: {
    cree: jour(-3), resume: 'Plan de test', conseils: ['Dormir'],
    semaines: [0, 1, 2].map((k) => ({
      debut: decale(lundi, 7 * k), theme: `Semaine ${k + 1}`,
      seances: [
        seance(`a${k}`, decale(lundi, 7 * k + 1), 'course', 'Fractionné', {
          intensite: 'soutenu',
          etapes: [{ type: 'echauffement', duree_s: 900, cible: { type: 'zone_fc', zone: 2 } },
            { type: 'repetition', repetitions: 5, etapes: [{ type: 'effort', duree_s: 180, cible: { type: 'zone_fc', zone: 4 } }, { type: 'recuperation', duree_s: 120 }] },
            { type: 'retour_au_calme', duree_s: 600 }],
          alternatives: [{ pour: 'Sur tapis', sport: 'course', titre: 'Fractionné tapis', duree: 45, intensite: 'soutenu' }],
        }),
        seance(`b${k}`, decale(lundi, 7 * k + 2), 'renfo', 'Renfo', { etat: k === 0 ? 'fait' : '', exercices: [{ nom: 'Squat', series: 3, repetitions: '10', repos_s: 60 }] }),
        seance(`c${k}`, decale(lundi, 7 * k + 4), 'velo', 'Home trainer', { etapes: [{ type: 'effort', duree_s: 1200, cible: { type: 'ftp', min: 70, max: 80 } }] }),
        seance(`d${k}`, decale(lundi, 7 * k + 6), 'trail', 'Sortie longue', { duree: 120 }),
      ],
    })),
  },
  cycle: {
    cree: jour(-3), objectif: { nom: 'Trail 42 km', date: jour(70) },
    phases: [{ nom: 'foncier', debut: jour(-10), fin: jour(20), but: 'Volume' }, { nom: 'developpement', debut: jour(21), fin: jour(48) },
      { nom: 'specifique', debut: jour(49), fin: jour(62) }, { nom: 'affutage', debut: jour(63), fin: jour(70) }, { nom: 'recuperation', debut: jour(71), fin: jour(77) }],
  },
  evenements: [{ id: 'e1', nom: 'Cross', date: jour(0), type: 'C', sport: 'course' }, { id: 'e2', nom: 'Semi', date: jour(30), type: 'B', notes: '21 km' },
    { id: 'e3', nom: 'Vacances', date: jour(40), fin: jour(45), type: 'indispo' }],
  memoire: [{ id: 'm1', date: jour(-5), categorie: 'sante', texte: 'Gêne au mollet gauche' }],
  avisDuJour: { date: jour(0), verdict: 'garder', explication: 'Bonne forme.' },
  avisSeances: { 1001: { date: jour(-1), texte: 'Bonne séance.' } },
  analyse: { date: jour(-2), texte: 'Analyse de test.' },
};

const REPARTITION = {
  semaines: [0, 1, 2, 3].map((k) => ({ debut: decale(lundi, -7 * k), secondes: [1800, 9000, 1200, 900, 300], sorties: 4 })),
  total: [7200, 36000, 4800, 3600, 1200], sans_detail: 2,
};
ETAT.zones = {
  date: jour(-1), fc_repos: 48,
  pied: {
    fc_max: { valeur: 188, methode: 'deuxième plus haute' }, bornes_fc: [140, 152, 165, 172],
    seuil1: { fc: 152, vitesse: 3.4, methode: 'dérive', confiance: 'moyenne' }, seuil2: { fc: 172, vitesse: 4.0, puissance: 300, methode: 'FC tenue', confiance: 'bonne' },
    zones: [1, 2, 3, 4, 5].map((z) => ({ zone: z, nom: `Z${z}`, fc: [120 + 10 * z, 130 + 10 * z], vitesse: [2.5 + z / 5, 2.7 + z / 5] })),
  },
};

// Petit serveur de fichiers pour docs/
function serveur() {
  return new Promise((ok) => {
    const s = http.createServer((req, res) => {
      const f = path.join(RACINE, decodeURIComponent(req.url.split('?')[0]).replace(/^\/$/, '/index.html'));
      if (!f.startsWith(RACINE) || !fs.existsSync(f)) { res.writeHead(404); return res.end(); }
      res.writeHead(200, { 'Content-Type': f.endsWith('.html') ? 'text/html; charset=utf-8' : 'application/octet-stream' });
      fs.createReadStream(f).pipe(res);
    }).listen(0, () => ok(s));
  });
}

async function verifier(navigateur, base, theme) {
  const erreurs = [];
  const page = await navigateur.newPage({ viewport: { width: 390, height: 844 }, colorScheme: theme });
  page.on('pageerror', (e) => erreurs.push(`erreur JS : ${e.message}${process.env.PILE ? "\n" + e.stack : ""}`));
  page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) erreurs.push(`console.error : ${m.text()}`); });
  await page.route('**/*', (r) => {
    const u = r.request().url();
    if (u.startsWith(base)) return r.continue();
    if (u.includes('/rest/v1/donnees')) return r.fulfill({ json: r.request().method() === 'GET' ? [{ data: ETAT }] : [] });
    if (u.includes('/rest/v1/activites')) return r.fulfill({ json: ACTIVITES });
    if (u.includes('/rest/v1/')) return r.fulfill({ json: [] });
    // Réponses du serveur dans leur vraie forme (voir zones.repartition(), garmin.py)
    if (u.includes('/garmin/repartition')) return r.fulfill({ json: REPARTITION });
    if (u.includes('/garmin/records')) return r.fulfill({ json: { records: [] } });
    if (u.includes('/importer')) return r.fulfill({ json: { nombre: 0 } });
    if (u.includes('/garmin/') || u.includes('/strava/')) return r.fulfill({ json: { connecte: true } });
    if (u.startsWith('https://cloach.pstrauss.org')) return r.fulfill({ body: '' });
    return r.abort();
  });
  await page.addInitScript((etat) => {
    localStorage.setItem('cloach-session', JSON.stringify({ access_token: 'a.b.c', refresh_token: 'x', expires_at: Date.now() / 1000 + 3600, user: { id: 'u', email: 'test@exemple.fr' } }));
    localStorage.setItem('cloach-etat', JSON.stringify(etat));
    localStorage.setItem('cloach-dernier-import', String(Date.now()));
  }, ETAT);
  await page.goto(base + '/index.html');
  await page.waitForTimeout(1500);

  const verifs = [];
  const attendu = async (nom, condition) => {
    let ok = false;
    try { ok = await condition; } catch { ok = false; }
    if (!ok) verifs.push(`${theme} : ${nom}`);
  };
  const liensClaude = async (ou) => {
    const morts = await page.$$eval(`${ou} [data-question], ${ou} a[target="_blank"]`, (as) => as
      .filter((a) => a.offsetParent !== null || a.closest('details'))
      .filter((a) => !/^https?:\/\//.test(a.getAttribute('href') || ''))
      .map((a) => a.textContent.trim().slice(0, 40) || a.outerHTML.slice(0, 60)));
    for (const m of morts) verifs.push(`${theme} : lien sans adresse dans ${ou} : « ${m} »`);
  };
  const largeur = async (onglet) => {
    const trop = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (trop > 1) verifs.push(`${theme} : défilement horizontal de ${trop} px dans l'onglet ${onglet}`);
  };

  try {
    // Calendrier
    await attendu('bandeau de 7 jours', page.$$eval('#bandeau button', (b) => b.length === 7));
    await attendu('détail du jour', page.$eval('#carteJour', (e) => e.textContent.length > 20));
    await attendu('repère du calendrier (Cross) dans le jour', page.$eval('#carteJour', (e) => e.textContent.includes('Cross')));
    const demande = await page.$('#carteJour .demande-avis summary');
    if (demande) {
      await demande.click();
      await page.fill('#carteJour .demande-avis textarea', 'Question test');
      await attendu('question en plus dans le lien de l\'avis', page.$eval('#carteJour .demande-avis [data-question-avis]', (a) => decodeURIComponent(a.href).includes('Question test')));
    } else verifs.push(`${theme} : pas de « Demander l'avis du coach » sous la sortie du jour`);
    await liensClaude('[data-vue="jour"]');
    await largeur('Calendrier');
    // Semaine précédente puis suivante
    await page.click('#semainePrecedente'); await page.waitForTimeout(200);
    await page.click('#semaineSuivante'); await page.waitForTimeout(200);

    // Plan
    await page.click('[data-onglet="plan"]'); await page.waitForTimeout(400);
    await attendu('en-tête du plan', page.$eval('#enTetePlan', (e) => e.textContent.includes('Mon plan')));
    await attendu('bande du cycle', page.$('#enTetePlan .bande-cycle'));
    await attendu('séances de la semaine', page.$$eval('#semaines .seance, #semaines [data-seance], #semaines li, #semaines details', (s) => s.length > 0));
    await attendu('bouton « réajuster » avec un lien vers Claude', page.$eval('#btnPlan', (a) => /^https:\/\/claude\.ai/.test(a.href)));
    for (const s of await page.$$('#semaines .compacte, #semaines summary')) { await s.click().catch(() => {}); }
    await liensClaude('[data-vue="plan"]');
    await largeur('Plan');

    // Stats : ouvrir toutes les cartes repliables
    await page.click('[data-onglet="stats"]'); await page.waitForTimeout(600);
    await attendu('tuiles des stats', page.$$eval('#tuiles > *', (t) => t.length >= 4));
    for (const d of await page.$$('[data-vue="stats"] details.repli:not([hidden]) > summary')) { await d.click(); await page.waitForTimeout(250); }
    await liensClaude('[data-vue="stats"]');
    await largeur('Stats');

    // Profil
    await page.click('[data-onglet="profil"]'); await page.waitForTimeout(400);
    await attendu('carte « Mon calendrier »', page.$eval('#evenements', (e) => e.textContent.includes('Semi')));
    await attendu('carte mémoire', page.$eval('#carteMemoire', (e) => !e.hidden));
    await liensClaude('[data-vue="profil"]');
    await largeur('Profil');

    // Retour au Calendrier : les liens Claude sont toujours bons après un réaffichage complet
    await page.evaluate(() => toutAfficher());
    await page.click('[data-onglet="jour"]'); await page.waitForTimeout(300);
    await liensClaude('body');

  } catch (e) {
    verifs.push(`${theme} : arrêt du test : ${e.message.split('\n')[0]}`);
  }

  await page.close();
  return [...erreurs.map((e) => `${theme} : ${e}`), ...verifs];
}

(async () => {
  const s = await serveur();
  const base = `http://localhost:${s.address().port}`;
  const navigateur = await chromium.launch();
  let problemes = [];
  try {
    for (const theme of ['light', 'dark']) {
      try { problemes = problemes.concat(await verifier(navigateur, base, theme)); }
      catch (e) { problemes.push(`${theme} : arrêt du test : ${e.message.split('\n')[0]}`); }
    }
  } finally {
    await navigateur.close();
    s.close();
  }
  const uniques = [...new Set(problemes)];
  if (uniques.length) {
    console.error(`✗ App : ${uniques.length} problème(s)\n  - ` + uniques.join('\n  - '));
    process.exit(1);
  }
  console.log('✓ App : quatre onglets, cartes ouvertes, liens Claude, clair et sombre, sans erreur');
})();
