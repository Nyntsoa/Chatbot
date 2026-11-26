import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge import answer_question, interpretation, normalized, seed_documents
from app.store import ROOT, Store


def run(mode, use_vectors=False, strict=False):
    questions = json.loads(
        (ROOT / "tests" / "test_questions.json").read_text(encoding="utf-8-sig")
    )
    retrieval = generation = 0
    with TemporaryDirectory() as directory:
        store = Store(Path(directory) / "evaluation.sqlite3")
        store.initialize()
        seed_documents(store)
        admin = store.bootstrap("evaluation", "Fixture GuideBot only!")
        for row in store.versions(admin, management=True):
            domains = row["domains"].split("|")
            store.update_version(
                admin,
                row["id"],
                "Source de test",
                "2020-01-01",
                "2099-12-31",
                "students",
                domains,
                submit=True,
            )
            store.publish(admin, row["id"])
        if use_vectors:
            from app.knowledge import index_vectors

            print(f"Passages indexés : {index_vectors(store, admin)}")

        for item in questions:
            question = item["question"]
            reply = answer_question(store, admin, question, semantic=use_vectors)
            keywords = [normalized(keyword) for keyword in item["expected_keywords"]]
            reference = item["reference_doc"]
            excerpts = " ".join(
                normalized(row["text"])
                for row in reply.sources
                if row["filename"] == reference
            )
            matched = any(keyword in excerpts for keyword in keywords)
            retrieval += matched
            print(
                f"[{item['id']:02d}/{len(questions)}] {'OK' if matched else 'NON'} · {question}"
            )
            if mode == "full" and reply.status in {"sourced", "partial"}:
                start = perf_counter()
                try:
                    response = normalized(interpretation(question, reply, "fr"))
                    found = any(keyword in response for keyword in keywords)
                    generation += found
                    print(
                        f"       {'OK' if found else 'NON'} · interprétation ({perf_counter() - start:.1f}s)"
                    )
                except (OSError, ValueError, KeyError) as error:
                    print(f"       Erreur de génération : {error}")
        print(
            f"Extraits avec le fichier et un terme attendus : {retrieval}/{len(questions)}"
        )
        if mode == "full":
            print(
                f"Interprétations avec un terme attendu : {generation}/{len(questions)}"
            )
    return int(
        strict
        and (
            retrieval != len(questions)
            or (mode == "full" and generation != len(questions))
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["retrieval", "full"], default="retrieval")
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="utiliser les vecteurs Ollama dans la base de test",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="retourner une erreur si un contrôle échoue",
    )
    options = parser.parse_args()
    raise SystemExit(run(options.mode, options.semantic, options.strict))
