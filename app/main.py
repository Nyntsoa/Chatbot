import os
from time import time
from urllib.parse import urlsplit

import streamlit as st

from app.admin import render_admin, render_classes
from app.appearance import THEME_LABELS, theme_markup
from app.config import connected_mode
from app.knowledge import folder_signature, seed_documents
from app.onboarding import render_onboarding
from app.store import ROLES, ROOT, Store
from app.student import (
    render_chat,
    render_history,
    render_home,
    render_library,
    render_services,
)

LOGO = ROOT / "images" / "GuideBot.png"
STYLES = ROOT / "app" / "interface.css"
SCHOOL_ISSUER = os.getenv("GUIDEBOT_SCHOOL_ISSUER") if connected_mode() else None


@st.cache_resource(show_spinner=False, max_entries=2)
def database(signature):
    store = Store()
    store.initialize()
    failures = seed_documents(store)
    return store, failures


def masthead():
    line = st.container(
        key="guidebot_masthead", horizontal=True, vertical_alignment="center"
    )
    if LOGO.exists():
        line.image(str(LOGO), width=42)
    line.markdown("# GuideBot")


def auth_layout(store):
    masthead()
    with st.container(key="auth_layout"):
        intro, form = st.columns([1.08, 1], gap="large")
        with intro, st.container(key="auth_intro"):
            st.html("""
                <div class="gb-intro">
                  <h2>Votre vie étudiante, au même endroit.</h2>
                  <p>Retrouvez vos documents de classe, posez vos questions et suivez vos démarches.</p>
                  <dl>
                    <div><dt>Étudiant</dt><dd>Rejoignez votre classe avec le code transmis par votre établissement.</dd></div>
                    <div><dt>Professeur</dt><dd>Utilisez votre code pour rejoindre la classe et déposer des documents.</dd></div>
                    <div><dt>Administrateur</dt><dd>Créez votre espace et invitez vos étudiants.</dd></div>
                  </dl>
                </div>
                """)
        with form, st.container(key="auth_form_panel", border=True):
            auth_screen(store)


def auth_screen(store):
    setup = store.needs_setup()
    if setup and (
        os.getenv("GUIDEBOT_WEB_SETUP", "1") != "1"
        or st.get_option("server.address") not in {"localhost", "127.0.0.1", "::1"}
        or (
            st.context.url
            and urlsplit(st.context.url).hostname
            not in {"localhost", "127.0.0.1", "::1"}
        )
    ):
        st.warning(
            "Le premier compte administrateur doit être créé depuis le terminal de l’hôte."
        )
        st.code("python -m app.manage setup", language="powershell")
        return
    signup = None
    if not setup:
        mode = st.segmented_control(
            "Accès à GuideBot",
            ["Connexion", "Créer un compte"],
            default="Connexion",
            required=True,
            width="stretch",
            label_visibility="collapsed",
            key="auth_mode",
        )
        if mode == "Créer un compte":
            signup = st.selectbox(
                "Type de compte",
                ["student", "manager", "admin"],
                format_func=lambda role: (
                    "Professeur" if role == "manager" else ROLES[role]
                ),
                key="signup_role",
            )
    if signup:
        st.header(
            f"Inscription · {ROLES[signup] if signup != 'manager' else 'Professeur'}"
        )
        st.caption(
            "Créez un espace privé pour vos documents et vos étudiants."
            if signup == "admin"
            else "Saisissez le code transmis par le responsable de votre classe."
        )
        with st.form("invited_signup"):
            code = (
                st.text_input("Code d’invitation", type="password", max_chars=80)
                if signup != "admin"
                else None
            )
            name = st.text_input("Identifiant", max_chars=80)
            password = st.text_input(
                "Mot de passe",
                type="password",
                max_chars=128,
                help="Au moins 12 caractères.",
            )
            confirm = st.text_input(
                "Confirmer le mot de passe", type="password", max_chars=128
            )
            if st.form_submit_button(
                "Créer le compte", type="primary", width="stretch"
            ):
                try:
                    if password != confirm:
                        raise ValueError("Les deux mots de passe ne correspondent pas.")
                    session = (
                        store.register_admin(name, password)
                        if signup == "admin"
                        else store.register_invited(name, password, signup, code)
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.session_state.clear()
                    st.session_state.auth = session
                    st.rerun()
        return
    st.header("Créer le premier compte" if setup else "Connexion")
    st.caption(
        "Ce compte administrateur gère les utilisateurs et valide les sources."
        if setup
        else "Accédez à la discussion, aux sources validées et à vos démarches."
    )
    with st.form("authentication", clear_on_submit=True):
        username = st.text_input("Identifiant", max_chars=80)
        password = st.text_input("Mot de passe", type="password", max_chars=128)
        confirm = (
            st.text_input("Confirmer le mot de passe", type="password", max_chars=128)
            if setup
            else password
        )
        if st.form_submit_button(
            "Créer le compte administrateur" if setup else "Se connecter",
            type="primary",
            width="stretch",
        ):
            try:
                if setup:
                    if password != confirm:
                        raise ValueError("Les deux mots de passe ne correspondent pas.")
                    store.bootstrap(username, password)
                session = store.authenticate(username, password)
                if not session:
                    st.error(
                        "Connexion refusée. Vérifiez vos identifiants ou réessayez dans deux minutes."
                    )
                    return
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state.clear()
                st.session_state.auth = session
                st.rerun()
    if (
        SCHOOL_ISSUER
        and not setup
        and st.button("Connexion établissement", width="stretch")
    ):
        st.login("school")


def school_account(store, claims):
    email = claims.get("email")
    expiration = claims.get("exp")
    if (
        not SCHOOL_ISSUER
        or claims.get("iss") != SCHOOL_ISSUER
        or claims.get("email_verified") is not True
        or not isinstance(email, str)
        or not isinstance(expiration, (int, float))
        or not expiration > time()
    ):
        return None
    return store.school_user(email)


def account_settings(store, user, theme):
    st.header("Mon compte")
    st.text(user["username"])
    st.caption(ROLES[user["role"]])
    if st.button(
        "Revoir le guide de démarrage", icon=":material/help:", key="reopen_guide"
    ):
        st.session_state.guide_open = True
        st.session_state.next_view = "Mon compte"
        st.rerun()
    with st.container(border=True, key="appearance_panel"):
        st.subheader("Apparence")

        def save_theme():
            store.save_appearance_preference(user["id"], st.session_state.account_theme)

        st.selectbox(
            "Thème",
            list(THEME_LABELS),
            index=list(THEME_LABELS).index(theme),
            format_func=THEME_LABELS.get,
            key="account_theme",
            on_change=save_theme,
        )
        st.caption("Votre choix est enregistré dans votre compte.")
    with st.form("change_password", clear_on_submit=True):
        old = st.text_input("Mot de passe actuel", type="password", max_chars=128)
        new = st.text_input("Nouveau mot de passe", type="password", max_chars=128)
        confirm = st.text_input(
            "Confirmer le nouveau mot de passe", type="password", max_chars=128
        )
        if st.form_submit_button("Modifier le mot de passe", type="primary"):
            try:
                if new != confirm:
                    raise ValueError("Les deux mots de passe ne correspondent pas.")
                revision = store.change_password(user["id"], old, new)
                st.session_state.auth = user["id"], revision
                st.success("Mot de passe modifié.")
            except ValueError as error:
                st.error(str(error))


def animate_navigation(selected, sections):
    previous = st.session_state.get("displayed_view")
    st.session_state.displayed_view = selected
    if previous not in sections or previous == selected:
        return
    order = list(sections)
    offset = "12px" if order.index(selected) > order.index(previous) else "-12px"
    destination = (
        ".st-key-chat_intro, .st-key-discussion_workspace"
        if selected == "Discussion"
        else f".st-key-routed_page_{order.index(selected)}"
    )
    st.html(f"""
        <style>
        @media (prefers-reduced-motion: no-preference) {{
            {destination} {{
                --gb-navigation-offset: {offset};
                animation: gb-navigation-slide 280ms cubic-bezier(.22, 1, .36, 1);
            }}
        }}
        </style>
    """)


def main():
    st.set_page_config(
        page_title="GuideBot · Vie étudiante",
        page_icon=str(LOGO) if LOGO.exists() else None,
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    st.html(STYLES)
    store, failures = database(folder_signature())
    credentials = st.session_state.get("auth")
    school_session = bool(SCHOOL_ISSUER and getattr(st.user, "is_logged_in", False))
    user = (
        school_account(store, st.user.to_dict())
        if school_session
        else (store.session_user(*credentials) if credentials else None)
    )
    if not user:
        if credentials:
            st.session_state.clear()
        if school_session:
            masthead()
            st.warning(
                "Ce compte établissement n’est pas autorisé ou sa session a expiré."
            )
            if st.button("Se déconnecter de l’établissement"):
                st.logout()
        else:
            with st.container(key="auth_scope"):
                auth_layout(store)
        return

    identifier = user["id"]
    theme = store.appearance_preference(identifier)
    st.html(theme_markup(theme))
    masthead()
    with st.sidebar:
        st.subheader("GuideBot")
        st.text(user["username"])
        st.caption(ROLES[user["role"]])
        if st.button("Se déconnecter", icon=":material/logout:", width="stretch"):
            st.session_state.clear()
            if school_session:
                st.logout()
            else:
                st.rerun()
    if st.session_state.get("guide_open") or store.needs_onboarding(identifier):
        render_onboarding(store, user)
        return
    if notice := st.session_state.pop("notice", None):
        st.success(notice)
    if warning := st.session_state.pop("warning_notice", None):
        st.warning(warning)
    unread = store.unread_updates(identifier)
    if unread:
        if st.button(
            f"Nouvelles publications ({unread})",
            type="tertiary",
            icon=":material/notifications:",
        ):
            st.session_state.next_view = "Démarches"
            st.session_state.open_updates = True
            st.rerun()
    sections = {
        "Accueil": lambda: render_home(store, identifier),
        "Discussion": lambda: render_chat(store, identifier),
        "Bibliothèque": lambda: render_library(store, identifier),
        "Classes": lambda: render_classes(store, identifier, user["role"]),
        "Démarches": lambda: render_services(store, identifier),
        "Historique": lambda: render_history(store, identifier),
    }
    if user["role"] != "student":
        sections["Administration"] = lambda: render_admin(
            store, identifier, user["role"]
        )
    sections["Mon compte"] = lambda: account_settings(store, user, theme)
    if target := st.session_state.pop("next_view", None):
        st.session_state.main_view = target
    selected = st.segmented_control(
        "Navigation",
        list(sections),
        default=None if "main_view" in st.session_state else "Accueil",
        key="main_view",
        label_visibility="collapsed",
        required=True,
        wrap=True,
    )
    if selected == "Administration" and user["role"] == "admin" and failures:
        st.warning(
            f"{len(failures)} fichier(s) TXT/PDF du dossier docs n’ont pas pu être importés."
        )
    if selected:
        animate_navigation(selected, sections)
        try:
            if selected == "Discussion":
                sections[selected]()
            else:
                with st.container(key=f"routed_page_{list(sections).index(selected)}"):
                    sections[selected]()
        except PermissionError:
            st.warning("Votre accès à cet élément a changé. Actualisez la page.")


if __name__ == "__main__":
    main()
