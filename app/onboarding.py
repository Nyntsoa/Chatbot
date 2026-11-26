import streamlit as st

PURPOSE = (
    "À quoi sert GuideBot ?",
    "GuideBot aide à trouver les informations de votre établissement sur les horaires, le règlement et les démarches. "
    "La discussion s’appuie sur les documents validés et accessibles dans votre espace.",
)
STEPS = {
    "student": (
        (
            "Retrouver les documents de votre classe",
            "Ouvrez **Classes** pour voir vos classes. Utilisez un code d’invitation si vous devez en rejoindre une. "
            "Dans **Bibliothèque**, ouvrez les documents validés ou recherchez-les par titre.",
        ),
        (
            "Poser une question et vérifier la réponse",
            "Dans **Discussion**, choisissez votre classe si nécessaire. Sous **Où chercher ?**, sélectionnez les documents de l’établissement ou, si le Web est activé, des sources publiques. "
            "Après votre question, ouvrez **Sources et détails** pour vérifier le passage ou le lien cité. Les actions **Pour aller plus loin** permettent d’approfondir la recherche.",
        ),
    ),
    "manager": (
        (
            "Déposer les documents utiles",
            "Dans **Administration → Documents**, téléversez le règlement, le calendrier ou la procédure, puis choisissez la classe concernée. "
            "Renseignez les dates de validité et soumettez le document à l’administration pour validation.",
        ),
        (
            "Inviter les étudiants et vérifier leurs sources",
            "Dans **Classes**, sélectionnez votre classe et générez un code étudiant à leur transmettre. "
            "Dans **Discussion**, sélectionnez cette classe et essayez une question pour vérifier les références citées.",
        ),
    ),
    "admin": (
        (
            "Préparer vos classes et vos documents",
            "Dans **Classes**, créez ou sélectionnez une classe. Dans **Administration → Documents**, déposez les documents, "
            "choisissez leur périmètre d’accès et validez leur publication pour que GuideBot puisse les utiliser.",
        ),
        (
            "Inviter les membres et tester GuideBot",
            "Dans **Classes**, générez un code pour les étudiants ou les professeurs de la classe, puis transmettez-le aux personnes concernées. "
            "Dans **Discussion**, testez une question et ouvrez les sources citées pour vérifier la réponse.",
        ),
    ),
}


def render_onboarding(store, user):
    steps = (PURPOSE, *STEPS[user["role"]])
    step = st.session_state.get("guide_step", 0)
    title, text = steps[step]

    with st.container(border=True, key="onboarding_panel"):
        st.header("Bienvenue sur GuideBot")
        st.progress(
            (step + 1) / len(steps),
            text=f"Guide de démarrage · étape {step + 1} sur {len(steps)}",
        )
        st.subheader(title)
        st.markdown(text)
        with st.container(horizontal=True, key="onboarding_actions"):
            if step and st.button(
                "Précédent", icon=":material/arrow_back:", key="guide_back"
            ):
                st.session_state.guide_step = step - 1
                st.rerun()
            last = step == len(steps) - 1
            proceed = st.button(
                "Terminer le guide" if last else "Suivant",
                icon=":material/check:" if last else ":material/arrow_forward:",
                type="primary",
                key="guide_next",
            )
            skip = st.button("Passer le guide", type="tertiary", key="guide_skip")
        if proceed and not last:
            st.session_state.guide_step = step + 1
            st.rerun()
        if skip or proceed:
            store.finish_onboarding(user["id"])
            st.session_state.pop("guide_open", None)
            st.session_state.pop("guide_step", None)
            st.rerun()
