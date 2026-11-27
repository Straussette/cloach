#!/usr/bin/env bash
# Vérifications à passer avant chaque push (lancées aussi par le crochet .githooks/pre-push) :
#  1. syntaxe du script de l'app ;
#  2. version de l'app augmentée si docs/index.html a changé par rapport à origin/main ;
#  3. serveur : compilation de tous les modules, outils MCP et fonctions pures (tests/serveur_test.py) ;
#  4. app dans un vrai navigateur : quatre onglets, cartes, liens Claude, clair et sombre (tests/app.test.js).
# Usage : tests/verifier.sh   (code de sortie 0 si tout passe)
cd "$(dirname "$0")/.." || exit 1
echecs=0
etape() { echo; echo "▶ $1"; }
rate() { echo "✗ $1"; echecs=$((echecs + 1)); }

etape "Syntaxe de l'app"
awk '/<script>/{f=1;next}/<\/script>/{f=0}f' docs/index.html > /tmp/cloach-app.js
if node --check /tmp/cloach-app.js; then echo "✓ Syntaxe"; else rate "Syntaxe du script de docs/index.html"; fi

etape "Version de l'app"
git fetch -q origin main 2>/dev/null
if git diff --quiet origin/main -- docs/index.html 2>/dev/null; then
  echo "✓ docs/index.html inchangé"
else
  avant=$(git show origin/main:docs/index.html 2>/dev/null | grep -o 'name="app-version" content="[^"]*"')
  apres=$(grep -o 'name="app-version" content="[^"]*"' docs/index.html)
  if [ "$avant" != "$apres" ]; then echo "✓ Version augmentée ($apres)"; else rate "docs/index.html a changé sans augmenter app-version"; fi
fi

etape "Serveur"
VENV=serveur/.venv
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" && "$VENV/bin/pip" install -q -r serveur/requirements.txt || rate "Installation des dépendances du serveur"
fi
if "$VENV/bin/python" -m py_compile serveur/*.py; then echo "✓ Compilation des modules"; else rate "Compilation du serveur"; fi
"$VENV/bin/python" tests/serveur_test.py || rate "Tests du serveur"

etape "App dans le navigateur"
NODE_PATH="${NODE_PATH:-$(npm root -g)}" node tests/app.test.js || rate "Tests de l'app"

echo
if [ "$echecs" -gt 0 ]; then echo "✗ $echecs vérification(s) en échec : ne pas pousser."; exit 1; fi
echo "✓ Tout est bon : on peut pousser."
