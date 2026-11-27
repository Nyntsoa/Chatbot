import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge import (
    ANSWER_MODEL,
    EMBEDDING_MODEL,
    answer_question,
    index_vectors,
    interpretation,
    normalized,
    seed_documents,
)
from app.store import ROOT, Store

QUESTIONS = ROOT / "tests" / "test_questions.json"


def check_answer(text, sources, case):
    citations = {int(number) for number in re.findall(r"\[(\d+)\]", text)}
    matching_sources = [
        row
        for number, row in enumerate(sources, 1)
        if number in citations and row["filename"] == case["reference_doc"]
    ]
    text = normalized(text)
    # Accepte 13h15, 13 h 15 et 13:15 comme le même horaire.
    text = re.sub(r"(\d{1,2})\s*[h:]\s*(\d{2})\b", r"\1h\2", text)
    missing = [fact for fact in case["expected_facts"] if not re.search(fact, text)]
    failures = []
    if not matching_sources or not citations <= set(range(1, len(sources) + 1)):
        failures.append("source attendue non citée ou référence invalide")
    if missing:
        failures.append(f"faits manquants ({', '.join(missing)})")
    return failures


def run(mode, use_vectors=False, strict=False, selected=()):
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8-sig"))
    if {case["id"] for case in questions} != set(range(1, 31)) or len(questions) != 30:
        raise ValueError("Le jeu d’évaluation P21 doit contenir les 30 identifiants attendus.")
    questions = [case for case in questions if not selected or case["id"] in selected]
    if not questions or set(selected) - {case["id"] for case in questions}:
        raise ValueError("Choisissez des identifiants de question entre 1 et 30.")
    totals = Counter(case["category"] for case in questions)
    retrieval, generation = Counter(), Counter()
    generated = 0
    print(f"Recherche : {'hybride (' + EMBEDDING_MODEL + ')' if use_vectors else 'lexicale'}")
    if mode == "full":
        print(f"Rédaction locale : {ANSWER_MODEL}")

    with TemporaryDirectory() as directory:
        store = Store(Path(directory) / "evaluation.sqlite3", clock=lambda: date(2026, 2, 1))
        store.initialize()
        if failures := seed_documents(store):
            raise ValueError(f"Documents d’évaluation illisibles : {', '.join(failures)}")
        password = "Fixture GuideBot only!"
        admin = store.bootstrap("evaluation", password)
        student = store.create_user(admin, "etudiant", password, "student")
        for row in store.versions(admin, management=True):
            store.update_version(
                admin,
                row["id"],
                "Source de test",
                "2020-01-01",
                "2099-12-31",
                "students",
                row["domains"].split("|"),
                submit=True,
            )
            store.publish(admin, row["id"])
        if use_vectors:
            count = index_vectors(store, admin)
            if not count:
                raise ValueError("Aucun passage indexé pour l’évaluation hybride.")
            print(f"Passages indexés : {count}")

        for case in questions:
            question, category = case["question"], case["category"]
            reply = answer_question(store, student, question, semantic=use_vectors)
            problems = check_answer(reply.answer, reply.sources, case)
            if reply.status != "sourced":
                problems.insert(0, f"statut obtenu {reply.status}")
            retrieval[category] += not problems
            print(
                f"[{case['id']:02d}] {'OK' if not problems else 'ÉCHEC'} · {question}",
                flush=True,
            )
            if problems:
                print(f"     Extrait : {' ; '.join(problems)}. {reply.answer}")

            if mode == "full":
                start = perf_counter()
                try:
                    draft = interpretation(question, reply, "fr")
                except (requests.RequestException, OSError, ValueError) as error:
                    print(
                        f"     Repli sur l’extrait ({perf_counter() - start:.1f} s) · {error}",
                        flush=True,
                    )
                    continue
                generated += 1
                issues = check_answer(draft, reply.sources, case)
                generation[category] += not issues
                print(
                    f"     IA {'OK' if not issues else 'ÉCHEC'} ({perf_counter() - start:.1f} s) · {draft}",
                    flush=True,
                )
                if issues:
                    print(f"     {' ; '.join(issues)}", flush=True)

    for category, total in totals.items():
        line = f"{category} · extraits {retrieval[category]}/{total}"
        if mode == "full":
            line += f" · IA {generation[category]}/{total}"
        print(line)
    print(f"Extraits affichés avec les faits et la source attendus : {retrieval.total()}/{len(questions)}")
    if mode == "full":
        print(f"Rédactions conformes aux critères : {generation.total()}/{len(questions)}")
        print(f"Rédactions affichables : {generated}/{len(questions)}")
    return int(strict and (retrieval != totals or (mode == "full" and generation != totals)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["retrieval", "full"], default="retrieval")
    parser.add_argument("--semantic", action="store_true", help="indexer les passages avec Ollama")
    parser.add_argument("--strict", action="store_true", help="échouer si un contrôle échoue")
    parser.add_argument(
        "--question",
        type=int,
        action="append",
        default=[],
        help="sélectionner un identifiant (répétable)",
    )
    options = parser.parse_args()
    try:
        raise SystemExit(run(options.mode, options.semantic, options.strict, options.question))
    except (requests.RequestException, OSError, ValueError) as error:
        parser.exit(2, f"Évaluation impossible : {error}\n")
