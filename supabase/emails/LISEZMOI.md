# E-mails de Cloach

Modèles des e-mails envoyés par Supabase Auth (projet `cloach`), à coller dans
Authentication → Emails → Templates. Les styles sont écrits en ligne, avec des couleurs fixes :
les messageries ne lisent ni les feuilles de style ni les variables CSS.

| Modèle Supabase | Objet | Fichier |
|---|---|---|
| Confirm signup | Confirme ton adresse Cloach | `confirmation.html` |
| Reset password | Ton nouveau mot de passe Cloach | `mot-de-passe.html` |
| Change email address | Confirme ta nouvelle adresse Cloach | `changement-email.html` |
| Magic link | Ton lien de connexion à Cloach | `lien-magique.html` |

Envoi par Resend (Authentication → Emails → SMTP Settings) : hôte `smtp.resend.com`, port 465,
utilisateur `resend`, mot de passe = clé API Resend (jamais dans ce dépôt), expéditeur
`cloach@envoi.pstrauss.org`, nom « Cloach ». Domaine `envoi.pstrauss.org` vérifié dans Resend (Ireland).
