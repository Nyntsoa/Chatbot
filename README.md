# GuideBot — assistant de la vie étudiante

GuideBot répond aux étudiants sur le **règlement**, l’**emploi du temps** et les **démarches de l’établissement**. Le livrable P21 associe Python, Streamlit, SQLite et Ollama sur la même machine. Les comptes, les conversations et les versions des documents sont conservés dans `data/guidebot.sqlite3`.

Le mode local est actif par défaut. La recherche Web, l’authentification externe et la base distante nécessitent le mode connecté. Les modèles Ollama sont interrogés sur `127.0.0.1:11434`. Après installation des dépendances et téléchargement des modèles, le parcours P21 fonctionne hors ligne.

## Démarrage

Depuis le dossier `p21-assistant` dans PowerShell :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:GUIDEBOT_MODE = "local"
.\.venv\Scripts\python.exe -m streamlit run app/main.py
```

Installez Ollama et téléchargez le modèle avec `ollama pull llama3.2:3b`. Pour lancer le moteur en mode local uniquement, quittez l’instance Ollama existante puis lancez dans un autre terminal :

```powershell
$env:OLLAMA_NO_CLOUD = "1"
$env:OLLAMA_HOST = "127.0.0.1:11434"
ollama serve
```

GuideBot vérifie que le modèle est installé au format GGUF avant de lui transmettre une question ou un document. La discussion affiche les extraits cités immédiatement. Cliquez sur **Reformuler avec Ollama** sous une réponse, ou activez la reformulation automatique dans les options. Sur une machine à mémoire limitée, `GUIDEBOT_ANSWER_MODEL=llama3.2:1b` permet d’utiliser le modèle plus léger après l’avoir téléchargé. [Configuration locale d’Ollama](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)

Ouvrez [GuideBot sur le port 8501](http://127.0.0.1:8501). Au premier lancement local, créez l’administrateur à l’écran. Choisissez un mot de passe d’au moins 12 caractères. Créez les invitations étudiants ou professeurs depuis **Classes**. Les autres administrateurs s’inscrivent depuis l’accueil.

| Rôle | Accès |
| --- | --- |
| Étudiant | Discussion, documents autorisés, historique personnel, démarches, rappels et signalements |
| Gestionnaire pédagogique | Préparation des procédures et calendriers, suivi des demandes de son périmètre et tableau de bord |
| Administrateur | Comptes, toutes les versions, publication, archivage, validation des réponses et configuration des services |

## Organisation du projet

| Emplacement | Fonction |
| --- | --- |
| `app/main.py`, `app/student.py`, `app/admin.py` | Interface Streamlit et parcours des étudiants et de l’administration |
| `app/store.py` | Comptes, droits par espace et classe, versions, historique et stockage SQLite |
| `app/knowledge.py` | Lecture des TXT et PDF, recherche des extraits, citations et rédaction par Ollama |
| `docs/`, `prompts/`, `tests/` | Documents de démonstration, consignes du modèle et évaluation P21 |

## Déploiement connecté facultatif

Le déploiement Vercel héberge l’application et la base hors de la machine de l’établissement. Le conteneur démarre Streamlit et Ollama et déclare `GUIDEBOT_MODE=connected`. Configurez `TURSO_DATABASE_URL` et `TURSO_AUTH_TOKEN` avec votre base. Pour créer le premier administrateur distant, exécutez `python -m app.manage setup` dans un terminal configuré avec ces variables et `GUIDEBOT_MODE=connected`. Les fichiers `data/` de l’installation locale sont exclus du déploiement.

## Classes et invitations

Sur l’écran d’accueil, **Créer un compte administrateur** est accessible sans code et ouvre un espace indépendant. Les documents, comptes étudiants, demandes et réglages de cet espace sont séparés de ceux des autres administrateurs.

Dans **Classes**, générez un code pour les étudiants ou les professeurs de votre espace. Le code étudiant permet jusqu’à 50 utilisations. Le code professeur est à usage unique. Les deux expirent après sept jours. L’invité saisit le code à l’inscription, puis se connecte avec son identifiant et son mot de passe. Les membres existants peuvent rejoindre une autre classe de leur espace depuis **Classes**.

Dans **Administration → Documents**, sélectionnez la classe sous **Accès au document** lors du dépôt. Une fois la version publiée par un administrateur, les membres peuvent sélectionner la classe dans **Discussion**. GuideBot limite alors la recherche à ses documents validés et aux documents communs du même espace et utilise Ollama pour rédiger une réponse à partir des extraits. Si Ollama est indisponible, les extraits restent consultables.

## Publier et rechercher

Les PDF avec texte sélectionnable et les fichiers TXT en UTF-8 sont importés en **brouillon** depuis `docs/`. Au prochain chargement de la page, un fichier ajouté ou modifié dans ce dossier produit un brouillon. Pour publier, renseignez l’auteur, les dates, le niveau de confidentialité et les domaines. Soumettez la version puis validez-la avec un compte administrateur. Publier un remplacement archive automatiquement la version précédente. Seule une version publiée et en cours de validité alimente les réponses. La bibliothèque et les exports appliquent les mêmes droits.

Les documents fournis servent à la démonstration. Le règlement et l’emploi du temps portent sur 2025–2026 et le PDF sur 2026–2027. Pour une utilisation réelle, publiez les versions officielles en cours avec leurs dates de validité. Le PDF de démonstration contient des jours incompatibles avec certaines dates. Le règlement parle de « 48 heures ouvrées » pour le justificatif médical, tandis que la procédure indique « 48 heures ». Ces indications demandent confirmation auprès de la scolarité avant publication officielle.

Pour ajouter la recherche sémantique locale :

```powershell
ollama pull embeddinggemma
```

Dans **Administration → Documents → Recherche sémantique**, cliquez sur **Actualiser l’index vectoriel** après l’ajout de versions. GuideBot combine ensuite les vecteurs disponibles avec la recherche lexicale. Les modèles tournent sur Ollama en local. Configurez leurs noms avec `GUIDEBOT_EMBED_MODEL` et `GUIDEBOT_ANSWER_MODEL` si nécessaire. Les sources et citations ont leur langue d’origine. L’aide conversationnelle et l’explication facultative peuvent être affichées en français ou en anglais.

Les passages sont cités par page ou ligne, avec le numéro d’article lorsqu’il existe. Les avertissements d’expiration et de contradiction servent au tri. Les explications d’Ollama sont identifiées comme des interprétations. Une réponse relue par un administrateur peut être réutilisée pour la même question tant que ses sources sont disponibles. Les signalements et décisions sont consultables dans **Administration → Validation**. Le tableau de bord affiche séparément la couverture documentaire, les votes et les décisions humaines.

**Discussion** recherche dans les documents de l’établissement accessibles à l’étudiant. En mode connecté, `GUIDEBOT_WEB_SEARCH=1` donne aussi accès aux modes **Web public** et **Documents et Web public**. Ces modes transmettent la requête actuelle aux moteurs publics et distinguent les sources Web des consignes de l’établissement. L’historique et l’export PDF conservent les citations.

## Démarches et intégrations

Les liens de procédure, de calendrier et de contact se règlent par domaine dans **Administration → Services**. Un portail académique, y compris Moodle, peut être lié depuis cette section. Les demandes sont enregistrées dans la boîte de réception interne. Le fichier de rappel `.ics` s’importe dans un calendrier. Un compteur avertit l’utilisateur des nouvelles versions qu’il peut consulter. Le détail figure dans **Démarches → Mises à jour documentaires**.

Dans **Mon compte → Apparence**, choisissez **Clair**, **Sombre**, **Forêt** ou **Contraste élevé**. Le choix est enregistré dans votre compte GuideBot. **Suivre l’appareil** applique automatiquement le mode clair ou sombre du système.

En mode connecté, l’authentification OpenID Connect est facultative. Pour l’activer, définissez `GUIDEBOT_MODE=connected` et `GUIDEBOT_SCHOOL_ISSUER` avec l’issuer exact, créez l’application côté établissement, puis renseignez les secrets Streamlit :

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
.\.venv\Scripts\python.exe -X utf8 tests/evaluate.py --strict
.\.venv\Scripts\python.exe -X utf8 tests/evaluate.py --semantic --strict
.\.venv\Scripts\python.exe -X utf8 tests/evaluate.py --mode full
```

L’import de l’historique conserve les fichiers JSON originaux. Les anciennes réponses consultables par leur destinataire portent la mention « sources non vérifiées ».

L’évaluation publie les documents de démonstration dans une base temporaire et pose les 30 questions avec un compte étudiant. Elle exige que l’extrait affiché contienne tous les faits attendus et cite le bon document. `--mode full` mesure aussi la rédaction Ollama. `--question 12` permet de rejouer un cas précis. Le contrôle automatique vérifie des motifs et des citations. La justesse du sens, les ajouts et la fidélité de la reformulation demandent une relecture. Les tests unitaires couvrent également les droits, la validité des sources et l’export. [Documentation des prompts](prompts/README.md)

### Résultats sur les documents de démonstration

| Sujet | Extraits en recherche lexicale | Extraits avec EmbeddingGemma | Reformulations avec Llama 3.2 3B |
| --- | ---: | ---: | ---: |
| Règlement | 10/10 | 10/10 | 6/10 |
| Emploi du temps | 10/10 | 10/10 | 9/10 |
| Procédures | 10/10 | 10/10 | 10/10 |
| **Total** | **30/30** | **30/30** | **25/30** |

Les reformulations ont été évaluées avec la recherche lexicale. Le modèle a par exemple omis « ouvrées » après « 48 heures » et l’heure de fin d’une pause déjeuner. La discussion affiche le passage cité avant la reformulation.
