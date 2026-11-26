import re
from collections import Counter
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
import streamlit as st
from pypdf.errors import PyPdfError

from app.exports import source_label
from app.knowledge import EMBEDDING_MODEL, index_vectors, parse_document
from app.store import DOMAINS, KINDS, ROLES, STATES, VISIBILITY


def save_action(operation, message):
    try:
        operation()
    except (ValueError, PermissionError, OSError, PyPdfError) as error:
        st.error(str(error))
    else:
        st.session_state["notice"] = message
        st.rerun()


def meta_fields(row, role):
    def initial(key):
        return date.fromisoformat(row[key]) if row and row[key] else None

    author = st.text_input(
        "Auteur ou service responsable", row["author"] if row else "", max_chars=160
    )
    first, last = st.columns(2)
    publication = first.date_input(
        "Publication", value=initial("publication"), min_value=date(2000, 1, 1)
    )
    expiration = last.date_input(
        "Expiration", value=initial("expiration"), min_value=date(2000, 1, 1)
    )
    choices = list(VISIBILITY) if role == "admin" else ["students", "staff"]
    selected = row["visibility"] if row else "students"
    visibility = st.selectbox(
        "Confidentialité",
        choices,
        index=choices.index(selected),
        format_func=VISIBILITY.get,
    )
    domain = st.multiselect(
        "Domaines autorisés",
        DOMAINS,
        default=row["domains"].split("|") if row and row["domains"] else [],
    )
    return (
        author,
        publication.isoformat() if publication else None,
        expiration.isoformat() if expiration else None,
        visibility,
        domain,
    )


def upload_versions(store, user, role, rows):
    st.subheader("Ajouter ou remplacer")
    replacement = st.selectbox(
        "Remplacer un document",
        [None, *{row["document_id"]: row["id"] for row in reversed(rows)}.values()],
        format_func=lambda identifier: (
            "Nouveaux documents"
            if identifier is None
            else next(row["title"] for row in rows if row["id"] == identifier)
        ),
    )
    old = next((row for row in rows if row["id"] == replacement), None)
    classrooms = {row["id"]: row["name"] for row in store.classrooms(user)}
    with st.form(f"upload_{replacement}"):
        classroom = (
            None
            if old
            else st.selectbox(
                "Accès au document",
                [None, *classrooms],
                index=1 if role == "manager" and classrooms else 0,
                format_func=lambda value: classrooms.get(value, "Documents communs"),
            )
        )
        files = st.file_uploader(
            "PDF ou TXT", type=["pdf", "txt"], accept_multiple_files=True
        )
        kinds = KINDS if role == "admin" or classrooms else ("Procédure", "Calendrier")
        kind = old["kind"] if old else st.selectbox("Type de document", kinds)
        metadata = meta_fields(old, role)
        if st.form_submit_button("Enregistrer en brouillon", type="primary"):

            def write_files():
                if (
                    len(files) > 15
                    or sum(file.size for file in files) > 50 * 1024 * 1024
                ):
                    raise ValueError(
                        "Un lot accepte jusqu’à 15 fichiers et 50 Mo au total."
                    )
                entries = []
                for file in files:
                    content = file.getvalue()
                    entries.append(
                        (file.name, content, parse_document(file.name, content))
                    )
                store.add_versions(
                    user,
                    entries,
                    kind,
                    *metadata,
                    replacement=replacement,
                    classroom=classroom,
                )

            save_action(
                write_files, "Brouillon enregistré. Soumettez-le quand il est prêt."
            )


def document_status(row, today):
    if row["status"] == "published" and row["expiration"] < today:
        return "Expirée"
    return STATES[row["status"]]


def manage_documents(store, user, role):
    st.subheader("Documents et versions")
    rows = store.versions(user, management=True)
    with st.expander("Ajouter ou remplacer des documents"):
        upload_versions(store, user, role, rows)
    if role == "admin":
        with st.expander("Recherche sémantique"):
            st.caption(
                f"Modèle local : {EMBEDDING_MODEL}. Les documents validés et soumis peuvent être indexés."
            )
            if st.button("Actualiser l’index vectoriel", icon=":material/sync:"):
                try:
                    with st.spinner("Indexation locale…"):
                        count = index_vectors(store, user)
                except (requests.RequestException, ValueError, KeyError, TypeError):
                    st.error(
                        "Ollama ou le modèle d’embeddings est indisponible. La recherche lexicale reste active."
                    )
                else:
                    st.success(f"{count} passage(s) indexé(s).")
    today = store.today()
    status = st.selectbox(
        "Afficher",
        [
            "Toutes les versions",
            "À valider",
            "Brouillons",
            "Validées et en cours",
            "Expirées",
            "Archivées",
        ],
    )
    filters = {
        "À valider": "À valider",
        "Brouillons": "Brouillon",
        "Validées et en cours": "Validée",
        "Expirées": "Expirée",
        "Archivées": "Archivée",
    }
    query = st.text_input("Rechercher un document ou un auteur")
    items = {
        row["id"]: row
        for row in rows
        if (status not in filters or document_status(row, today) == filters[status])
        and query.strip().casefold()
        in f"{row['filename']} {row['title']} {row['author']}".casefold()
    }
    if not items:
        st.info("Aucune version dans cette sélection.")
        return
    identifier = st.selectbox(
        "Version à consulter",
        list(items),
        format_func=lambda item: (
            f"{items[item]['title']} · v{items[item]['number']} · {document_status(items[item], today)}"
        ),
    )
    row = items[identifier]
    st.text(row["classroom"] or "Documents communs")
    st.caption(f"{row['filename']} · {VISIBILITY[row['visibility']]} · {row['kind']}")
    if row["validator"]:
        st.caption(f"Validée par {row['validator']} le {row['validated_at'][:10]}")
    with st.expander("Prévisualiser le document"):
        _, passages = store.version_preview(user, identifier, management=True)
        with st.container(height=360, border=True):
            for passage in passages:
                st.caption(passage["locator"])
                st.text(passage["text"])
    if row["status"] in {"draft", "pending"}:
        with st.form(f"metadata_{identifier}"):
            metadata = meta_fields(row, role)
            save = st.form_submit_button("Enregistrer en brouillon")
            submit = st.form_submit_button("Soumettre à validation")
            if save or submit:
                save_action(
                    lambda: store.update_version(
                        user, identifier, *metadata, submit=submit
                    ),
                    "Version soumise à validation."
                    if submit
                    else "Métadonnées enregistrées.",
                )
    else:
        st.text(f"Auteur : {row['author'] or 'À renseigner'}")
        st.caption(
            f"Publication {row['publication'] or '—'} · validité jusqu’au {row['expiration'] or '—'}"
        )
    if role == "admin" and row["status"] == "pending":
        if st.button(
            "Valider et publier",
            type="primary",
            icon=":material/check_circle:",
            key=f"publish_{identifier}",
        ):
            save_action(
                lambda: store.publish(user, identifier),
                "Version publiée. Elle est disponible selon sa confidentialité.",
            )
    if role == "admin" and row["status"] != "archived":
        with st.expander("Archiver la version"):
            st.caption(
                "Elle sera retirée de la bibliothèque et de la recherche. Son historique restera consultable en administration."
            )
            if st.button("Confirmer l’archivage", key=f"archive_{identifier}"):
                save_action(
                    lambda: store.archive(user, identifier), "Version archivée."
                )


def manage_accounts(store, user):
    st.subheader("Comptes et rôles")
    st.caption(
        "Pour inscrire un étudiant ou un professeur, générez un code dans Classes."
    )
    accounts = {row["id"]: row for row in store.accounts(user)}
    identifier = st.selectbox(
        "Compte à gérer",
        list(accounts),
        format_func=lambda item: accounts[item]["username"],
    )
    row = accounts[identifier]
    with st.form(f"edit_account_{identifier}"):
        role = st.selectbox(
            "Rôle",
            [row["role"]] if row["role"] == "admin" else ["student", "manager"],
            index=0
            if row["role"] == "admin"
            else ["student", "manager"].index(row["role"]),
            format_func=ROLES.get,
            disabled=identifier == user,
        )
        active = st.checkbox(
            "Compte actif", value=bool(row["active"]), disabled=identifier == user
        )
        password = st.text_input(
            "Nouveau mot de passe (facultatif)", type="password", max_chars=128
        )
        if st.form_submit_button("Enregistrer le compte"):
            save_action(
                lambda: store.update_user(user, identifier, role, active, password),
                "Compte mis à jour.",
            )


def manage_links(store, user):
    st.subheader("Liens des services")
    domain = st.selectbox("Domaine à configurer", DOMAINS)
    saved = store.links(user, domain)
    with st.form(f"links_{domain}"):
        procedure = st.text_input(
            "Procédure ou portail académique (HTTPS)",
            saved["procedure_url"] if saved else "",
        )
        email = st.text_input("Adresse du service", saved["contact"] if saved else "")
        calendar = st.text_input(
            "Calendrier (HTTPS)", saved["calendar_url"] if saved else ""
        )
        if st.form_submit_button("Enregistrer les liens", type="primary"):
            save_action(
                lambda: store.set_links(
                    user, domain, procedure.strip(), email.strip(), calendar.strip()
                ),
                "Liens mis à jour.",
            )


def review_answers(store, user):
    st.subheader("Réponses à examiner")
    queue = store.review_queue(user)
    if not queue:
        st.info("Aucun signalement ni aucune demande de validation en attente.")
    else:
        by_id = {item["id"]: item for item in queue}
        identifier = st.selectbox(
            "Réponse à examiner",
            list(by_id),
            format_func=lambda value: by_id[value]["question"][:90],
        )
        entry = by_id[identifier]
        st.text(entry["question"])
        st.caption(
            f"{entry['domain'] or 'Domaine à préciser'} · {entry['created_at'][:10]}"
        )
        st.text(entry["answer"])
        if entry["explanation"]:
            st.caption("Interprétation automatique affichée à l’étudiant")
            st.text(entry["explanation"])
        if entry["report"]:
            st.caption(
                f"{entry['reporter_count']} compte(s) ont signalé cette question."
            )
            st.warning(entry["report"])
        sources = store.review_sources(user, identifier)
        with st.expander("Sources actuelles", expanded=True):
            if not sources:
                st.info("Aucune source actuelle pour cette réponse.")
            for number, row in enumerate(sources, 1):
                st.caption(
                    f"[{number}] {source_label(row)} · publié le {row['publication']}"
                )
                st.text(row["text"])
        with st.form(f"review_queue_{identifier}"):
            answer = st.text_area(
                "Réponse à publier ou note de clôture", max_chars=1000
            )
            approve = st.form_submit_button(
                "Publier la réponse validée", type="primary", disabled=not sources
            )
            reject = st.form_submit_button("Clôturer avec une note")
            if approve or reject:
                save_action(
                    lambda: store.approve_review(
                        user, identifier, answer, "approved" if approve else "rejected"
                    ),
                    "Décision enregistrée dans l’historique.",
                )
    with st.expander("Historique des décisions"):
        for row in store.review_history(user):
            st.caption(
                f"Réponse {row['turn_id']} · {row['username']} · {row['created_at'][:10]} · "
                + {
                    "approved": "Validée",
                    "rejected": "Clôturée",
                    "pending": "Soumise",
                    "processed": "Traitée",
                }[row["state"]]
            )
            if row["answer"]:
                st.text(row["answer"])
    with st.expander("Journal des opérations"):
        for row in store.audit_log(user):
            st.text(
                f"{row['created_at'][:16].replace('T', ' ')} UTC · {row['username'] or 'Import initial'} · {row['action']} · {row['item']}"
            )


def manage_requests(store, user):
    st.subheader("Demandes reçues")
    rows = store.requests(user, staff=True)
    if not rows:
        st.info("Aucune demande dans votre périmètre.")
        return
    by_id = {row["id"]: row for row in rows}
    identifier = st.selectbox(
        "Demande à consulter",
        list(by_id),
        format_func=lambda item: (
            f"Demande {item} · {by_id[item]['domain']} · {by_id[item]['status']}"
        ),
    )
    row = by_id[identifier]
    st.text(row["message"])
    with st.form(f"ticket_{identifier}"):
        statuses = ("Reçue", "En cours", "Traitée")
        status = st.selectbox("Statut", statuses, index=statuses.index(row["status"]))
        response = st.text_area("Réponse au demandeur", row["response"], max_chars=2000)
        if st.form_submit_button("Enregistrer", type="primary"):
            save_action(
                lambda: store.handle_request(user, identifier, status, response),
                "Demande mise à jour.",
            )


def dashboard(store, user):
    st.subheader("Utilisation de GuideBot")
    days = st.selectbox(
        "Période",
        [7, 30, 90],
        index=1,
        format_func=lambda value: f"{value} derniers jours",
    )
    turns, documents = store.metrics(user, days)
    reviews = [row["review_state"] for row in turns if row["review_state"]]
    useful = [row["useful"] for row in turns if row["useful"] is not None]
    covered = sum(row["result"] in {"sourced", "reviewed", "partial"} for row in turns)
    count = len(turns)
    for widget, (label, value) in zip(
        st.columns(4),
        [
            ("Questions", count),
            ("Réponses étayées", f"{covered}/{count}" if count else "—"),
            ("Avis utiles", f"{sum(useful)}/{len(useful)}" if useful else "—"),
            (
                "Validations humaines",
                f"{reviews.count('approved')}/{len(reviews)}" if reviews else "—",
            ),
        ],
    ):
        widget.metric(label, value, border=False)
    if not turns:
        st.info("Les indicateurs apparaîtront après les premières questions.")
    else:
        try:
            local = ZoneInfo(st.context.timezone or "Indian/Antananarivo")
        except (ZoneInfoNotFoundError, KeyError):
            local = ZoneInfo("UTC")
        topics = Counter(turn["domain"] or "Sans domaine" for turn in turns)
        hours = Counter(
            datetime.fromisoformat(turn["created_at"]).astimezone(local).hour
            for turn in turns
        )
        left, right = st.columns(2)
        with left:
            st.markdown("**Sujets des demandes**")
            for topic, total in topics.most_common():
                st.text(f"{topic} · {total}")
        with right:
            st.markdown("**Questions par heure**")
            st.caption(str(local))
            st.bar_chart(
                [hours[hour] for hour in range(24)],
                y_label="Questions",
                x_label="Heure",
                height=190,
            )
        questions = Counter()

        def question_label(question):
            return re.sub(
                r"[\w.+-]+@[\w.-]+|\d(?:[\s.-]?\d){7,}",
                "[masqué]",
                question.strip().casefold(),
            )

        questions.update(question_label(turn["question"]) for turn in turns)
        with st.expander("Questions fréquentes et sans réponse"):
            for question, total in questions.most_common(8):
                st.text(f"{total} · {question}")
            misses = Counter(
                row["result"]
                for row in turns
                if row["result"] in {"unanswered", "expired", "conflict", "partial"}
            )
            for state, label in {
                "unanswered": "Sans réponse",
                "expired": "Sources expirées",
                "conflict": "Contradictions",
                "partial": "Réponses partielles",
            }.items():
                st.text(f"{label} · {misses[state]}")
            st.text(f"Réponses signalées · {sum(bool(row['report']) for row in turns)}")
            for row in (
                item
                for item in turns
                if item["result"] in {"unanswered", "expired", "conflict", "partial"}
            ):
                st.text(question_label(row["question"])[:180])
    with st.expander("Utilisation et validité des documents", expanded=not turns):
        if not documents:
            st.caption("Aucune version publiée à mesurer.")
        for row in sorted(documents, key=lambda item: -item["uses"]):
            flag = "À jour"
            if row["status"] == "archived":
                flag = "Archivée"
            elif row["expiration"] and row["expiration"] < store.today():
                flag = "Expirée"
            usage = (
                f"{row['uses']} utilisation(s)"
                if row["uses"]
                else "Jamais utilisée sur cette période"
            )
            st.text(f"{row['filename']} · v{row['number']} · {flag} · {usage}")


def render_admin(store, user, role):
    st.header("Administration" if role == "admin" else "Espace pédagogique")
    sections = {
        "Documents": lambda: manage_documents(store, user, role),
        "Demandes": lambda: manage_requests(store, user),
        "Tableau de bord": lambda: dashboard(store, user),
    }
    if role == "admin":
        sections.update(
            {
                "Validation": lambda: review_answers(store, user),
                "Comptes": lambda: manage_accounts(store, user),
                "Services": lambda: manage_links(store, user),
            }
        )
    selected = st.selectbox("Section", list(sections), key="staff_section")
    sections[selected]()


def render_classes(store, user, role):
    st.header("Classes")
    classrooms = {row["id"]: row["name"] for row in store.classrooms(user)}
    if classrooms:
        with st.container(border=True):
            st.subheader("Mes classes")
            for name in classrooms.values():
                st.text(name)
    else:
        st.info("Aucune classe pour le moment.")
    if role == "admin":
        with st.expander(
            "Créer une classe", expanded=not classrooms, icon=":material/add:"
        ):
            with st.form("new_class"):
                name = st.text_input(
                    "Nom de la classe",
                    max_chars=80,
                    placeholder="Ex. Licence 1 · Groupe A",
                )
                if st.form_submit_button("Créer la classe", type="primary"):
                    save_action(
                        lambda: store.create_classroom(user, name), "Classe créée."
                    )
    if role != "admin":
        with st.form("join_class"):
            code = st.text_input("Code d’invitation", type="password", max_chars=80)
            if st.form_submit_button("Rejoindre une classe"):
                save_action(
                    lambda: store.join_classroom(user, code),
                    "Vous avez rejoint la classe.",
                )
    if role not in {"admin", "manager"}:
        return
    if not classrooms:
        return
    st.subheader("Inviter dans une classe")
    roles = ["student", "manager"] if role == "admin" else ["student"]
    target = st.selectbox(
        "Type d’invitation",
        roles,
        format_func=lambda value: "Professeur" if value == "manager" else ROLES[value],
    )
    classroom = st.selectbox(
        "Classe de l’invitation", list(classrooms), format_func=classrooms.get
    )
    st.caption(
        "Valable 7 jours. Jusqu’à 50 inscriptions pour un code étudiant. Un seul usage pour un professeur."
    )
    if st.button("Générer un code", type="primary"):
        try:
            code = store.issue_invitation(user, target, classroom)
        except (ValueError, PermissionError) as error:
            st.error(str(error))
        else:
            st.code(code, language=None)
