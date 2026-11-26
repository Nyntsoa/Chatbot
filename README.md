# GuideBot — assistant de la vie étudiante

GuideBot fonctionne sur une machine locale. Les comptes, les conversations et les versions des documents sont stockés dans SQLite (`data/guidebot.sqlite3`). La recherche fonctionne aussi sans Ollama.

## Démarrage

Depuis le dossier `p21-assistant` dans PowerShell :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app/main.py
```

Ouvrez [GuideBot sur le port 8501](http://127.0.0.1:8501). Au premier lancement local, créez l’administrateur à l’écran. Choisissez un mot de passe d’au moins 12 caractères. Créez les invitations étudiants ou professeurs depuis **Classes**. Les autres administrateurs s’inscrivent depuis l’accueil.

| Rôle | Accès |
| --- | --- |
| Étudiant | Discussion, documents autorisés, historique personnel, démarches, rappels et signalements |
| Gestionnaire pédagogique | Préparation des procédures et calendriers, suivi des demandes de son périmètre et tableau de bord |
| Administrateur | Comptes, toutes les versions, publication, archivage, validation des réponses et configuration des services |

## Déploiement Vercel

Sélectionnez le preset **Container** avec Fluid Compute. `Dockerfile.vercel` démarre Streamlit sur le port fourni par Vercel et lance Ollama dans le même conteneur. Connectez une base [Turso](https://vercel.com/marketplace/tursocloud/database) au projet pour fournir `TURSO_DATABASE_URL` et `TURSO_AUTH_TOKEN`. Exécutez `python -m app.manage setup` dans un terminal configuré avec ces variables pour créer le premier administrateur de cette base. Les fichiers `data/` de l’installation locale sont exclus du déploiement.

## Classes et invitations

Sur l’écran d’accueil, **Créer un compte administrateur** est accessible sans code et ouvre un espace indépendant. Les documents, comptes étudiants, demandes et réglages de cet espace sont séparés de ceux des autres administrateurs.

Dans **Classes**, générez un code pour les étudiants ou les professeurs de votre espace. Le code étudiant permet jusqu’à 50 utilisations. Le code professeur est à usage unique. Les deux expirent après sept jours. L’invité saisit le code à l’inscription, puis se connecte avec son identifiant et son mot de passe. Les membres existants peuvent rejoindre une autre classe de leur espace depuis **Classes**.

Dans **Administration → Documents**, sélectionnez la classe sous **Accès au document** lors du dépôt. Une fois la version publiée par un administrateur, les membres peuvent sélectionner la classe dans **Discussion**. GuideBot limite alors la recherche à ses documents validés et aux documents communs du même espace et utilise Ollama pour rédiger une réponse à partir des extraits. Si Ollama est indisponible, les extraits restent consultables.

## Publier et rechercher

Les PDF avec texte sélectionnable et les fichiers TXT en UTF-8 sont importés en **brouillon** depuis `docs/`. Au prochain chargement de la page, un fichier ajouté ou modifié dans ce dossier produit un brouillon. Pour publier, renseignez l’auteur, les dates, le niveau de confidentialité et les domaines. Soumettez la version puis validez-la avec un compte administrateur. Publier un remplacement archive automatiquement la version précédente. Seule une version publiée et en cours de validité alimente les réponses. La bibliothèque et les exports appliquent les mêmes droits.

Pour l’index vectoriel et les explications facultatives :

```powershell
ollama pull embeddinggemma
ollama pull llama3.2:1b
```

Dans **Administration → Documents → Recherche sémantique**, cliquez sur **Actualiser l’index vectoriel** après l’ajout de versions. GuideBot combine ensuite les vecteurs disponibles avec la recherche lexicale. Les modèles tournent sur Ollama en local. Configurez leurs noms avec `GUIDEBOT_EMBED_MODEL` et `GUIDEBOT_ANSWER_MODEL` si nécessaire. Les sources et citations ont leur langue d’origine. L’aide conversationnelle et l’explication facultative peuvent être affichées en français ou en anglais.

Les passages sont cités par page ou ligne, avec le numéro d’article lorsqu’il existe. Les avertissements d’expiration et de contradiction servent au tri. Les explications d’Ollama sont identifiées comme des interprétations. Une réponse relue par un administrateur peut être réutilisée pour la même question tant que ses sources sont disponibles. Les signalements et décisions sont consultables dans **Administration → Validation**. Le tableau de bord affiche séparément la couverture documentaire, les votes et les décisions humaines.

Dans **Discussion → Où chercher ?**, le mode **Documents de l’établissement** est sélectionné par défaut. Il fonctionne hors ligne et respecte les droits de la classe. **Web public** cherche des pages et articles indexés. **Documents et Web public** présente les deux types de sources avec des références distinctes. L’historique et l’export PDF conservent les citations. Les extraits Web proviennent des moteurs de recherche et les liens permettent de consulter les articles complets. Quand les résultats sont insuffisants, GuideBot élargit la recherche dans le périmètre choisi. Les raccourcis sous la dernière réponse permettent d’approfondir ou de changer de source.

Les modes Web envoient la requête actuelle à des moteurs publics. Les documents de classe et l’historique restent sur la machine. Définissez `GUIDEBOT_WEB_SEARCH=0` avant de lancer GuideBot pour une installation exclusivement locale. Les réponses issues du Web ne peuvent pas être approuvées comme consignes officielles de l’établissement.

## Démarches et intégrations

Les liens de procédure, de calendrier et de contact se règlent par domaine dans **Administration → Services**. Un portail académique, y compris Moodle, peut être lié depuis cette section. Les demandes sont enregistrées dans la boîte de réception interne. Le fichier de rappel `.ics` s’importe dans un calendrier. Un compteur avertit l’utilisateur des nouvelles versions qu’il peut consulter. Le détail figure dans **Démarches → Mises à jour documentaires**.

Dans **Mon compte → Apparence**, choisissez **Clair**, **Sombre**, **Forêt** ou **Contraste élevé**. Le choix est enregistré dans votre compte GuideBot. **Suivre l’appareil** applique automatiquement le mode clair ou sombre du système.

L’authentification de l’établissement est facultative. Pour l’activer avec un fournisseur OpenID Connect, configurez `GUIDEBOT_SCHOOL_ISSUER` avec l’issuer exact, créez l’application côté établissement, puis renseignez les secrets Streamlit :

```toml
[auth]
redirect_uri = "https://guidebot.votre-ecole.example/oauth2callback"
cookie_secret = "remplacer-par-un-secret-aleatoire"

[auth.school]
client_id = "identifiant-fourni-par-etablissement"
client_secret = "secret-fourni-par-etablissement"
server_metadata_url = "https://identite.votre-ecole.example/.well-known/openid-configuration"
```

Le fournisseur doit envoyer une adresse e-mail vérifiée et une expiration. Créez au préalable le compte local avec cette adresse comme identifiant. Le rôle reste géré dans GuideBot. Avant une installation derrière un proxy, définissez `GUIDEBOT_WEB_SETUP=0` et utilisez `python -m app.manage setup` sur l’hôte. Les comptes locaux restent disponibles.

## Maintenance et tests

```powershell
# Importer les nouveaux fichiers du dossier docs/ en brouillon
.\.venv\Scripts\python.exe -m app.manage import-docs

# Compléter l’index à partir du terminal
.\.venv\Scripts\python.exe -m app.manage index <identifiant-admin>

# Attribuer explicitement les anciennes discussions locales à un compte
.\.venv\Scripts\python.exe -m app.manage import-history <identifiant-admin> <compte-destinataire>

.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests/evaluate.py --mode retrieval --strict
.\.venv\Scripts\python.exe tests/evaluate.py --mode retrieval --semantic
```

L’import de l’historique conserve les fichiers JSON originaux. Les anciennes réponses consultables par leur destinataire portent la mention « sources non vérifiées ». L’évaluation se déroule dans une base temporaire. Elle vérifie que les résultats contiennent le fichier attendu et un mot-clé attendu. Avec `--mode full`, elle lance aussi les explications Ollama. Les tests unitaires vérifient des réponses exactes et leurs repères, les accès, les versions, la fraîcheur, les désaccords et les exports.
