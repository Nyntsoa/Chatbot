import argparse
import hashlib
import json
from getpass import getpass

from app.knowledge import index_vectors, seed_documents
from app.store import ROOT, Store


def read_archives():
    for path in sorted((ROOT / "history").glob("*.json")):
        try:
            raw = path.read_bytes()
            data = json.loads(raw)
            messages = data["messages"]
            if not isinstance(messages, list):
                continue
            turns = []
            question = None
            for message in messages:
                if not isinstance(message, dict) or not isinstance(
                    message.get("content"), str
                ):
                    continue
                if message.get("role") == "user":
                    if question:
                        turns.append((question, ""))
                    question = message["content"][:700]
                elif question and message.get("role") == "assistant":
                    turns.append((question, message["content"][:5000]))
                    question = None
            if question:
                turns.append((question, ""))
            if turns:
                title = data.get("title")
                title = (
                    title[:70] if isinstance(title, str) and title else turns[0][0][:70]
                )
                yield hashlib.sha256(raw).hexdigest(), title, turns
        except (OSError, ValueError, KeyError, TypeError):
            continue


def main():
    parser = argparse.ArgumentParser(description="Administration locale de GuideBot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="créer le premier compte administrateur")
    sub.add_parser(
        "import-docs",
        help="importer en brouillon les nouveaux fichiers du dossier docs",
    )
    index = sub.add_parser("index", help="compléter l’index vectoriel")
    index.add_argument("admin")
    history = sub.add_parser(
        "import-history", help="attribuer les anciennes discussions à un compte"
    )
    history.add_argument("admin")
    history.add_argument("owner")
    options = parser.parse_args()
    store = Store()
    store.initialize()
    if options.command == "setup":
        if not store.needs_setup():
            parser.exit(1, "Un compte existe déjà.\n")
        username = input("Identifiant du premier administrateur : ")
        password = getpass("Mot de passe (12 caractères minimum) : ")
        if password != getpass("Confirmer : "):
            parser.exit(1, "Les mots de passe ne correspondent pas.\n")
        store.bootstrap(username, password)
        print("Compte administrateur créé.")
    elif options.command == "import-docs":
        skipped = seed_documents(store)
        print(
            "Import des nouveaux documents terminé.",
            f"Fichiers ignorés : {', '.join(skipped)}" if skipped else "",
        )
    else:
        session = store.authenticate(
            options.admin, getpass("Mot de passe administrateur : ")
        )
        if not session:
            parser.exit(1, "Connexion refusée.\n")
        if options.command == "index":
            print(f"{index_vectors(store, session[0])} passage(s) indexé(s).")
        else:
            print(
                f"{store.import_history(session[0], options.owner, list(read_archives()))} discussion(s) importée(s)."
            )


if __name__ == "__main__":
    main()
