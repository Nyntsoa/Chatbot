import re
from datetime import date, datetime, time
from html import escape
from pathlib import Path

import requests
import streamlit as st

from app.exports import reminder_file, response_pdf, source_label
from app.knowledge import Result, infer_domain, interpretation
from app.research import SCOPES, followup_questions, run_research, web_enabled
from app.store import DOMAINS

STATUS_LABELS = {
    "sourced": "Extraits de sources validées",
    "reviewed": "Réponse validée par l’administration",
    "partial": "Informations partielles",
    "conflict": "Vérification nécessaire",
    "expired": "Source expirée",
    "unanswered": "Information indisponible",
    "web_sourced": "Extraits du Web public",
    "legacy": "Ancienne réponse · sources non vérifiées",
}
QUESTIONS = {
    "Absences": "Quel justificatif fournir après une absence médicale ?",
    "Examens": "Quand ont lieu les examens finaux de la session 1 ?",
    "Stages": "Quelle est la durée minimale du stage de L2 ?",
    "Emploi du temps": "À quelles heures ont lieu les travaux pratiques ?",
}


def document_download(store, user, document, scope, label="Télécharger le document"):
    name = document["filename"]
    suffix = Path(name).suffix.lower()
    st.download_button(
        label,
        lambda: store.version_preview(user, document["id"])[0],
        file_name=name,
        mime="application/pdf" if suffix == ".pdf" else "text/plain",
        key=f"download_{scope}_{document['id']}",
        icon=":material/download:",
        on_click="ignore",
    )


def reading_text(text, *, arriving=False):
    paragraphs = re.split(r"\n\s*\n", text.strip())
    markup = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
    classes = "gb-reading gb-reply-arrival" if arriving else "gb-reading"
    st.html(f'<div class="{classes}">{markup}</div>')


def open_view(view):
    st.session_state.next_view = view
    st.rerun()


def open_library(version_id):
    st.session_state.library_target = version_id
    open_view("Bibliothèque")


def render_home(store, user):
    documents = sorted(
        store.versions(user),
        key=lambda item: item["validated_at"] or item["publication"],
        reverse=True,
    )
    classrooms = store.classrooms(user)
    with st.container(border=True, key="home_intro"):
        st.header("Que cherchez-vous aujourd’hui ?")
        st.markdown(
            "Règlement, horaires et démarches, avec les références de votre établissement."
        )
        if st.button(
            "Poser une question",
            icon=":material/chat_bubble:",
            type="primary",
            key="home_chat",
        ):
            open_view("Discussion")

    with st.container(key="home_workspace"):
        library, classes = st.columns([1.65, 1], gap="large")
        with library, st.container(border=True, key="home_documents"):
            st.subheader("Documents récents")
            if not documents:
                st.caption("Vos documents apparaîtront ici après validation.")
            for row in documents[:3]:
                if st.button(
                    row["title"],
                    icon=":material/description:",
                    width="stretch",
                    wrap=True,
                    key=f"home_document_{row['id']}",
                ):
                    open_library(row["id"])
                st.caption(f"{row['kind']} · {row['classroom'] or 'Espace commun'}")
            if st.button(
                "Ouvrir la bibliothèque",
                icon=":material/arrow_forward:",
                type="tertiary",
                key="home_library",
            ):
                open_view("Bibliothèque")
        with classes, st.container(border=True, key="home_classes"):
            st.subheader("Vos classes")
            if not classrooms:
                st.caption("Aucune classe associée à votre compte pour le moment.")
            for row in classrooms[:3]:
                st.text(row["name"])
            if st.button(
                "Ouvrir les classes",
                icon=":material/arrow_forward:",
                type="tertiary",
                key="home_classes_link",
            ):
                open_view("Classes")


def render_library(store, user):
    st.header("Bibliothèque")
    st.caption("Documents validés de votre espace")
    documents = store.versions(user)
    target = st.session_state.pop("library_target", None)
    by_id = {row["id"]: row for row in documents}
    if target is not None:
        if target in by_id:
            st.session_state.library_search = ""
            st.session_state.library_domain = None
            st.session_state.library_document = target
        else:
            st.warning("Cette source n’est plus disponible dans votre bibliothèque.")

    with st.container(key="library_workspace"):
        index, reader = st.columns([1, 2.15], gap="large")
        with index, st.container(border=True, key="library_index"):
            search = st.text_input(
                "Rechercher un document",
                placeholder="Titre, fichier ou auteur",
                key="library_search",
            )
            domain = st.selectbox(
                "Domaine",
                [None, *DOMAINS],
                format_func=lambda value: value or "Tous les domaines",
                placeholder="Tous les domaines",
                key="library_domain",
            )
            term = search.strip().casefold()
            matches = {
                row["id"]: row
                for row in documents
                if (not domain or domain in (row["domains"] or "").split("|"))
                and term
                in f"{row['title']} {row['filename']} {row['author']}".casefold()
            }
            if matches:
                st.caption(f"{len(matches)} document{'s' if len(matches) > 1 else ''}")
                if st.session_state.get("library_document") not in matches:
                    st.session_state.library_document = next(iter(matches))
                choice = st.selectbox(
                    "Document à lire",
                    list(matches),
                    key="library_document",
                    format_func=lambda value: matches[value]["title"],
                )
        with reader:
            if not matches:
                st.info(
                    "Aucun document ne correspond à ces filtres."
                    if documents
                    else "Les documents apparaîtront ici après validation par votre établissement."
                )
                return
            row = matches[choice]
            with st.container(border=True, key="library_reader"):
                st.subheader(row["title"])
                st.text(row["author"])
                st.caption(
                    f"Publié le {row['publication']} · valide jusqu’au {row['expiration']} · version {row['number']}"
                )
                document_download(store, user, row, "library")
                _, passages = store.version_preview(user, row["id"])
                with st.container(border=True, key="library_preview"):
                    for passage in passages:
                        st.caption(passage["locator"])
                        reading_text(passage["text"])


def render_sources(store, user, turn):
    sources = turn["sources"]
    public = turn.get("web_sources", [])
    if not sources and not public:
        return
    if sources:
        first = sources[0]
        st.caption(f"[1] {source_label(first)} · publié le {first['publication']}")
    elif public:
        st.caption(f"[1] {public[0]['host']} · source Web")
    with st.expander(
        f"Sources et détails ({len(sources) + len(public)})",
        icon=":material/menu_book:",
    ):
        for index, row in enumerate(sources, 1):
            st.text(f"[{index}] {source_label(row)}")
            st.caption(
                f"Auteur : {row['author']} · publication : {row['publication']} · valide jusqu’au : {row['expiration']}"
            )
            reading_text(row["text"])
            if st.button(
                "Lire dans la bibliothèque",
                icon=":material/open_in_new:",
                key=f"source_{turn['id']}_{index}",
            ):
                open_library(row["version_id"])
        unique = {row["version_id"]: row for row in sources}
        for identifier, row in unique.items():
            if row["kind"] == "Formulaire":
                document_download(
                    store,
                    user,
                    {"id": identifier, "filename": row["filename"]},
                    turn["id"],
                    "Télécharger le formulaire",
                )
            elif row["kind"] == "Calendrier":
                document_download(
                    store,
                    user,
                    {"id": identifier, "filename": row["filename"]},
                    turn["id"],
                    "Ouvrir le calendrier (fichier)",
                )
            elif row["kind"] == "Procédure":
                document_download(
                    store,
                    user,
                    {"id": identifier, "filename": row["filename"]},
                    turn["id"],
                    "Voir la procédure (fichier)",
                )
        if public:
            st.markdown("**Sources Web publiques**")
            for index, source in enumerate(public, len(sources) + 1):
                st.text(f"[{index}] {source['title']}")
                headline = source.get("kind") == "press_headline"
                provenance = [source.get("publisher") or source["host"]]
                if headline:
                    provenance.append("Titre de presse · via Google Actualités")
                elif source.get("kind") == "press_extract":
                    provenance.append("Extrait de presse")
                if source.get("date"):
                    provenance.append(source["date"])
                st.text(" · ".join(provenance))
                if not headline:
                    reading_text(source["text"])
                st.link_button(
                    f"Ouvrir la source Web [{index}]",
                    source["url"],
                    icon=":material/open_in_new:",
                )


def answer_actions(store, user, turn, displayed_answer):
    identifier = turn["id"]
    with st.expander("Actions sur la réponse"):
        st.caption("Copier le texte")
        st.code(displayed_answer, language=None, wrap_lines=True)
        st.download_button(
            "Exporter en PDF",
            lambda: response_pdf(store.export_turn(user, identifier)),
            file_name=f"guidebot-reponse-{identifier}.pdf",
            mime="application/pdf",
            key=f"pdf_{identifier}",
            icon=":material/picture_as_pdf:",
            on_click="ignore",
        )
        if turn["domain"] and st.button(
            "Ouvrir les démarches du domaine", key=f"service_{identifier}"
        ):
            st.session_state.service_domain = turn["domain"]
            st.session_state.next_view = "Démarches"
            st.rerun()
        useful, not_useful = st.columns(2)
        for choice, column, label in [
            (True, useful, "Utile"),
            (False, not_useful, "Pas utile"),
        ]:
            if column.button(
                label,
                key=f"vote_{identifier}_{choice}",
                type="primary" if turn["useful"] == int(choice) else "secondary",
                width="stretch",
            ):
                store.vote(user, identifier, choice)
                st.rerun()

        with st.form(f"report_{identifier}"):
            note = st.text_area(
                "Signaler un problème", max_chars=1000, value=turn["report"] or ""
            )
            if st.form_submit_button("Envoyer le signalement"):
                try:
                    store.report(user, identifier, note)
                    st.success("Signalement enregistré pour examen.")
                except ValueError as error:
                    st.error(str(error))
        if (
            turn["sources"]
            and (turn.get("scope") or "school") == "school"
            and turn["result"] not in {"conflict", "reviewed"}
        ):
            if st.button("Demander une validation humaine", key=f"review_{identifier}"):
                try:
                    store.propose_review(user, identifier, "Demande de validation")
                    st.success("La réponse a été transmise à l’administration.")
                except ValueError as error:
                    st.info(str(error))

        public = turn.get("web_sources", [])
        if turn["result"] in {"sourced", "partial", "web_sourced"} and (
            turn["sources"] or public
        ):
            explanation = turn.get("explanation")
            if not explanation and st.button(
                "Obtenir une explication avec Ollama", key=f"explain_{identifier}"
            ):
                result = Result(turn["answer"], turn["result"], turn["sources"])
                try:
                    with st.spinner("Préparation de l’explication…"):
                        explanation = interpretation(
                            turn["question"], result, turn["language"], public
                        )
                        store.save_explanation(user, identifier, explanation)
                    st.rerun()
                except (requests.RequestException, ValueError, OSError, KeyError):
                    st.warning(
                        "L’explication locale est indisponible. Les sources restent consultables."
                    )
                    explanation = None


def discussion_context(store, user):
    classrooms = {row["id"]: row["name"] for row in store.classrooms(user)}
    with st.container(border=True, key="discussion_context"):
        st.subheader("Contexte de recherche")
        scope = st.radio(
            "Où chercher ?",
            list(SCOPES) if web_enabled() else ["school"],
            format_func=SCOPES.get,
            key="research_scope",
        )
        if scope != "school":
            st.caption(
                "Les moteurs publics reçoivent uniquement la requête de recherche."
            )
        classroom = None
        if classrooms:
            classroom = st.selectbox(
                "Classe",
                [None, *classrooms],
                index=1,
                key="chat_classroom",
                format_func=lambda value: classrooms.get(value, "Toutes mes classes"),
                placeholder="Toutes mes classes",
                on_change=lambda: st.session_state.pop("conversation", None),
            )
        else:
            st.caption("La recherche porte sur les documents validés de votre espace.")
        with st.expander("Options de recherche", icon=":material/tune:"):
            st.selectbox(
                "Domaine",
                [None, *DOMAINS],
                key="chat_domain",
                format_func=lambda value: value or "Détection automatique",
                placeholder="Détection automatique",
            )
            st.selectbox(
                "Langue de l’explication",
                ["fr", "en"],
                key="language",
                format_func=lambda value: {"fr": "Français", "en": "English"}[value],
            )
            st.caption("Les citations restent dans la langue du document.")
            use_ai = st.checkbox(
                "Réponse rédigée par l’IA",
                value=True,
                key="chat_use_ai",
            )
    return classroom, use_ai, scope


def discussion_turn(store, user, turn, *, arriving=False):
    with st.chat_message("user"):
        reading_text(turn["question"])
    with st.chat_message("assistant"):
        if turn["sources"] is None:
            st.warning("Les sources de cette réponse ne sont plus disponibles.")
            return
        answer = turn.get("explanation") or turn["answer"]
        label = STATUS_LABELS[turn["result"]]
        if turn.get("explanation"):
            label = "Explication de l’IA à partir des sources"
        st.caption(label)
        scope = turn.get("scope") or "school"
        st.caption(f"Périmètre · {SCOPES[scope]}")
        web_state = turn.get("web_state")
        if web_state == "found":
            count = turn["web_queries"]
            st.caption(
                f"Web public · {count} requête{'s' if count != 1 else ''} · extraits indexés"
            )
        elif web_state in {"unavailable", "disabled", "empty"}:
            st.caption(
                {
                    "empty": "Web public · aucune source assez pertinente",
                    "disabled": "Web public · recherche désactivée",
                    "unavailable": "Web public · moteurs indisponibles",
                }[web_state]
            )
        reading_text(answer, arriving=arriving)
        if turn.get("explanation"):
            with st.expander("Extraits à l’origine de la réponse"):
                reading_text(turn["answer"])
        render_sources(store, user, turn)
        if turn["result"] != "legacy":
            answer_actions(store, user, turn, answer)


def discussion_suggestions(turn):
    st.markdown("**Pour aller plus loin**")
    for index, question in enumerate(followup_questions(turn)):
        if st.button(
            question,
            key=f"followup_{turn['id']}_{index}",
            icon=":material/arrow_forward:",
            wrap=True,
        ):
            return question, "school", False
    scope = turn.get("scope") or "school"
    if scope == "school":
        if st.button(
            "Approfondir dans les documents",
            icon=":material/search:",
            key="research_again_school",
        ):
            return turn["question"], "school", True
        if web_enabled():
            if st.button(
                "Relancer cette question sur le Web",
                icon=":material/language:",
                key="research_on_web",
            ):
                return turn["question"], "both", True
            st.caption(
                "Le raccourci Web envoie la question actuelle à un moteur public."
            )
    else:
        if web_enabled() and st.button(
            "Approfondir la recherche Web",
            icon=":material/search:",
            key="research_again_web",
        ):
            return turn["question"], scope, True
        if st.button(
            "Vérifier dans les documents de l’établissement",
            icon=":material/description:",
            key="research_verify_school",
            wrap=True,
        ):
            return turn["question"], "school", True
    if turn["result"] in {"unanswered", "expired", "conflict", "partial"}:
        st.caption(
            "Pour affiner la recherche, précise la classe ou la période."
            if scope == "school"
            else "Pour affiner la recherche, précise le sujet, le pays ou la période qui t’intéresse."
        )
    return None


def render_chat(store, user):
    conversation = st.session_state.get("conversation")
    with st.container(key="chat_intro"):
        title, actions = st.columns([3, 1], vertical_alignment="center")
        title.header("Poser une question")
        title.caption("Règlement, horaires et démarches, avec leurs sources.")
        if conversation and actions.button(
            "Nouvelle discussion",
            icon=":material/add:",
            width="stretch",
            key="new_chat",
        ):
            st.session_state.pop("conversation", None)
            st.session_state.pop("chosen_domain", None)
            st.rerun()

    turns = store.conversation(user, conversation)
    arriving = st.session_state.pop("reply_arrival", None) == conversation and bool(
        turns
    )
    choice = quick_domain = None
    next_research = None
    with st.container(key="discussion_workspace"):
        settings, content = st.columns([1, 2.35], gap="large")
        with settings:
            classroom, use_ai, scope = discussion_context(store, user)
        with content, st.container(key="discussion_content"):
            if turns:
                for turn in turns:
                    discussion_turn(
                        store, user, turn, arriving=arriving and turn is turns[-1]
                    )
                next_research = discussion_suggestions(turns[-1])
            else:
                st.subheader("Questions fréquentes")
                selected = st.pills(
                    "Filtrer les exemples",
                    list(QUESTIONS),
                    key="chosen_domain",
                    wrap=True,
                    label_visibility="collapsed",
                )
                suggestions = [
                    (name, text)
                    for name, text in QUESTIONS.items()
                    if not selected or name == selected
                ]
                with st.container(key="chat_shortcuts"):
                    for name, text in suggestions:
                        if st.button(
                            text,
                            icon=":material/arrow_forward:",
                            width="stretch",
                            wrap=True,
                            key=f"suggest_{name}",
                        ):
                            choice, quick_domain = text, name
    typed = st.chat_input("Votre question…", max_chars=700)
    question = (typed or choice or "").strip()
    deepen = False
    if next_research:
        question, scope, deepen = next_research
    if not question:
        return
    selected = (
        quick_domain
        or st.session_state.get("chat_domain")
        or (st.session_state.get("chosen_domain") if not turns else None)
    )
    domain = selected or infer_domain(question)
    language = st.session_state.get("language", "fr")
    try:
        with st.spinner(f"Recherche · {SCOPES[scope]}…"):
            research = run_research(
                store,
                user,
                question,
                scope,
                domain=selected,
                turns=turns,
                language=language,
                classroom=classroom,
                deepen=deepen,
            )
            result = research.result
            explanation = None
            if (
                use_ai
                and (result.sources or research.web_sources)
                and result.status in {"sourced", "partial", "web_sourced"}
            ):
                try:
                    explanation = interpretation(
                        question, result, language, research.web_sources
                    )
                except (requests.RequestException, ValueError, OSError, KeyError):
                    st.session_state.warning_notice = "GuideBot affiche les extraits cités en attendant une explication exploitable."
            st.session_state.conversation = store.save_turn(
                user,
                conversation,
                question,
                domain,
                language,
                result.answer,
                result.status,
                [row["id"] for row in result.sources],
                explanation=explanation,
                scope=scope,
                web_state=research.web_state,
                web_queries=research.web_queries,
                web_sources=research.web_sources,
            )
    except (PermissionError, ValueError) as error:
        st.warning(str(error))
    else:
        st.session_state.reply_arrival = st.session_state.conversation
        st.rerun()


def render_history(store, user):
    st.header("Historique")
    if st.button("Nouvelle discussion", icon=":material/add:", type="primary"):
        st.session_state.pop("conversation", None)
        st.session_state.pop("chosen_domain", None)
        st.session_state.next_view = "Discussion"
        st.rerun()
    query = st.text_input("Rechercher dans les conversations")
    rows = store.conversations(user, query)
    if not rows:
        st.info("Aucune conversation trouvée.")
        return
    for row in rows[:40]:
        left, right = st.columns([3, 1], vertical_alignment="center")
        left.text(row["title"])
        left.caption(row["updated_at"][:10])
        if right.button("Ouvrir", key=f"open_{row['id']}", width="stretch"):
            st.session_state.conversation = row["id"]
            st.session_state.pop("chosen_domain", None)
            st.session_state.next_view = "Discussion"
            st.rerun()
        if right.button(
            "Retirer de mon historique", key=f"forget_{row['id']}", width="stretch"
        ):
            store.forget_conversation(user, row["id"])
            if st.session_state.get("conversation") == row["id"]:
                st.session_state.pop("conversation", None)
            st.session_state.notice = "Discussion retirée de votre historique."
            st.rerun()
        st.divider()


def dated_updates(store, user):
    latest = sorted(
        (row for row in store.versions(user) if row["validated_at"]),
        key=lambda row: row["validated_at"],
        reverse=True,
    )[:5]
    if not latest:
        st.info("Les documents récemment validés apparaîtront ici.")
    for row in latest:
        st.text(f"{row['title']} · version {row['number']}")
        st.caption(
            f"Validée le {row['validated_at'][:10]} · valide jusqu’au {row['expiration']}"
        )


def render_services(store, user):
    st.header("Démarches")
    default = st.session_state.pop("service_domain", "Contacts")
    domain = st.selectbox("Service par domaine", DOMAINS, index=DOMAINS.index(default))
    links = store.links(user, domain)
    if links and any(
        links[key] for key in ("procedure_url", "contact", "calendar_url")
    ):
        if links["procedure_url"]:
            st.link_button(
                "Voir la procédure",
                links["procedure_url"],
                icon=":material/open_in_new:",
            )
        if links["contact"]:
            st.link_button(
                "Contacter le service",
                f"mailto:{links['contact']}",
                icon=":material/mail:",
            )
        if links["calendar_url"]:
            st.link_button(
                "Ouvrir le calendrier",
                links["calendar_url"],
                icon=":material/calendar_month:",
            )
    else:
        st.caption("Aucun lien de service n’a été configuré pour ce domaine.")
    with st.expander("Envoyer une demande interne"):
        with st.form("request", clear_on_submit=True):
            message = st.text_area("Votre demande", max_chars=2000)
            if st.form_submit_button("Envoyer ma demande", type="primary"):
                try:
                    store.create_request(user, domain, message)
                    st.success("Demande déposée dans GuideBot.")
                except ValueError as error:
                    st.error(str(error))
    with st.expander("Créer un rappel"):
        title = st.text_input("Titre du rappel", value=domain, max_chars=100)
        moment = st.date_input("Date", value=date.today(), min_value=date.today())
        hour = st.time_input("Heure", value=time(9))
        st.caption("Importez le fichier dans votre calendrier. L’heure est locale.")
        st.download_button(
            "Télécharger le rappel",
            lambda: reminder_file(title, datetime.combine(moment, hour), domain),
            file_name="guidebot-rappel.ics",
            mime="text/calendar",
            on_click="ignore",
            disabled=not title.strip(),
        )
    with st.expander("Mes demandes"):
        requests_list = store.requests(user)
        if not requests_list:
            st.caption("Aucune demande envoyée.")
        for row in requests_list:
            st.text(f"{row['domain']} · {row['status']}")
            st.caption(row["created_at"][:10])
            st.text(row["message"])
            if row["response"]:
                st.text(row["response"])
            st.divider()
    with st.expander(
        "Mises à jour documentaires",
        expanded=st.session_state.pop("open_updates", False),
    ):
        dated_updates(store, user)
        if store.unread_updates(user) and st.button(
            "Marquer les publications comme lues"
        ):
            store.mark_updates_read(user)
            st.rerun()
