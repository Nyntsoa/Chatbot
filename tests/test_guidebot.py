import tempfile
import unittest
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.exports import reminder_file, response_pdf
from app.knowledge import (
    EMBEDDING_MODEL,
    Result,
    answer_question,
    interpretation,
    parse_document,
)
from app.research import run_research, search_public
from app.store import Store

TODAY = date(2026, 11, 25)
START, END = "2026-09-01", "2027-08-31"
PASSWORD = "GuideBot QA 2026!"


class GuideBotTests(unittest.TestCase):
    def test_research_keeps_school_local_and_tries_web_again_on_weak_results(self):
        with patch("app.research.public_results") as engine:
            local = run_research(
                self.store, self.student, "Quelle est la durée du stage ?", "school"
            )
            self.assertEqual(local.web_state, "not_requested")
            engine.assert_not_called()
            engine.side_effect = [
                [
                    {
                        "href": "http://127.0.0.1/info",
                        "title": "Rapport de stage",
                        "body": "Rapport de stage",
                    }
                ],
                [
                    {
                        "href": "https://universite.example/stage#plan",
                        "title": "Rapport de stage",
                        "body": "Voici le plan du rapport de stage.",
                    }
                ],
            ]
            hits, state, attempts = search_public(
                "Peux-tu expliquer le rapport de stage ? test@example.org"
            )
        self.assertEqual((state, attempts), ("found", 2))
        self.assertEqual(hits[0]["url"], "https://universite.example/stage")
        for call in engine.call_args_list:
            self.assertNotIn("test@example.org", call.args[0])
        self.assertNotEqual(
            engine.call_args_list[0].args, engine.call_args_list[1].args
        )

    def test_mixed_research_preserves_provenance_and_access(self):
        self.add(
            "stage.txt",
            "Le rapport de stage doit être remis avant le 18 juin.",
            ["Stages"],
        )
        public = {
            "href": "https://presse.example/guide",
            "title": "Rapport de stage",
            "body": "Le rapport de stage expose les missions.",
            "source": "Journal local",
        }
        question = "Quand remettre le rapport de stage ?"
        with patch("app.research.public_results", return_value=[public]) as engine:
            research = run_research(
                self.store,
                self.student,
                question,
                "both",
                turns=[{"question": "information privée"}],
            )
        self.assertTrue(research.result.sources)
        self.assertEqual(engine.call_args_list[0].args, (question,))
        self.assertIn("18 juin", research.result.answer)
        self.assertIn("Journal local", research.result.answer)
        conversation = self.store.save_turn(
            self.student,
            None,
            question,
            "Stages",
            "fr",
            research.result.answer,
            research.result.status,
            [row["id"] for row in research.result.sources],
            scope=research.scope,
            web_state=research.web_state,
            web_queries=research.web_queries,
            web_sources=research.web_sources,
        )
        turn = self.store.conversation(self.student, conversation)[0]
        self.assertEqual(turn["scope"], "both")
        self.assertEqual(turn["sources"][0]["filename"], "stage.txt")
        self.assertEqual(turn["web_sources"][0]["publisher"], "Journal local")
        export = self.store.export_turn(self.student, turn["id"])
        pdf_text = " ".join(
            page.extract_text()
            for page in PdfReader(BytesIO(response_pdf(export))).pages
        )
        self.assertIn("https://presse.example/guide", pdf_text)
        with self.assertRaises(ValueError):
            self.store.propose_review(self.student, turn["id"], "Validation")
        with self.assertRaises(PermissionError):
            self.store.export_turn(self.manager, turn["id"])

    def test_web_explanation_rejects_invented_references(self):
        public = [
            {"host": "universite.example", "text": "Le rapport présente les missions."}
        ]
        with patch(
            "app.knowledge._ollama_json",
            return_value={
                "response": '{"answer":"Consulte le calendrier.","references":[8]}'
            },
        ):
            with self.assertRaises(ValueError):
                interpretation(
                    "Que contient le rapport ?", Result("", "web_sourced"), "fr", public
                )
        with patch(
            "app.knowledge._ollama_json",
            return_value={
                "response": '{"answer":"Le rapport presente les missions.","references":[1]}'
            },
        ):
            self.assertTrue(
                interpretation(
                    "Que contient le rapport ?", Result("", "web_sourced"), "fr", public
                ).endswith("[1]")
            )
        with (
            patch.dict("os.environ", {"GUIDEBOT_WEB_SEARCH": "0"}),
            patch("app.research.public_results") as engine,
        ):
            research = run_research(
                self.store, self.student, "Que contient le rapport ?", "web"
            )
            self.assertEqual(research.web_state, "disabled")
            engine.assert_not_called()

    def test_onboarding_is_pending_only_until_acknowledged(self):
        self.assertTrue(self.store.needs_onboarding(self.student))
        self.store.finish_onboarding(self.student)
        self.store.finish_onboarding(self.student)
        reopened = Store(self.store.path)
        reopened.initialize()
        self.assertFalse(reopened.needs_onboarding(self.student))
        self.assertTrue(reopened.needs_onboarding(self.admin))

    def test_appearance_is_persistent_and_account_specific(self):
        self.assertEqual(self.store.appearance_preference(self.admin), "system")
        self.store.save_appearance_preference(self.admin, "forest")
        self.store.save_appearance_preference(self.student, "contrast")
        reopened = Store(self.store.path)
        self.assertEqual(reopened.appearance_preference(self.admin), "forest")
        self.assertEqual(reopened.appearance_preference(self.student), "contrast")
        with self.assertRaises(ValueError):
            reopened.save_appearance_preference(self.student, "unknown")
        reopened.save_appearance_preference(self.admin, "system")
        self.assertEqual(reopened.appearance_preference(self.admin), "system")
        self.assertEqual(reopened.appearance_preference(self.student), "contrast")

    def test_dashboard_does_not_share_unattributed_questions(self):
        self.store.create_classroom(self.admin, "Classe A")
        classroom = self.store.classrooms(self.admin)[0]["id"]
        self.store.join_classroom(
            self.student, self.store.issue_invitation(self.admin, "student", classroom)
        )
        self.store.join_classroom(
            self.manager, self.store.issue_invitation(self.admin, "manager", classroom)
        )
        blob = "Le stage dure 12 semaines.".encode()
        document = self.store.add_versions(
            self.admin,
            [("stage.txt", blob, parse_document("stage.txt", blob))],
            "Procédure",
            "École",
            START,
            END,
            "students",
            ["Stages"],
            classroom=classroom,
        )[0]
        self.store.update_version(
            self.admin,
            document,
            "École",
            START,
            END,
            "students",
            ["Stages"],
            submit=True,
        )
        self.store.publish(self.admin, document)
        answer = answer_question(
            self.store,
            self.student,
            "Combien de semaines pour le stage ?",
            semantic=False,
            classroom=classroom,
        )
        self.store.save_turn(
            self.student,
            None,
            "Durée du stage",
            "Stages",
            "fr",
            answer.answer,
            answer.status,
            [row["id"] for row in answer.sources],
        )
        self.store.save_turn(
            self.student,
            None,
            "Question sans source",
            None,
            "fr",
            "Sans réponse",
            "unanswered",
            [],
        )
        turns, documents = self.store.metrics(self.manager, 30)
        self.assertEqual([row["question"] for row in turns], ["Durée du stage"])
        self.assertEqual(documents[0]["uses"], 1)
        self.assertEqual(len(self.store.metrics(self.admin, 30)[0]), 2)

    def test_database_read_uses_consistent_snapshot(self):
        with self.store.connect() as reader:
            self.assertEqual(
                reader.execute("SELECT COUNT(*) FROM classrooms").fetchone()[0], 0
            )
            self.store.create_classroom(self.admin, "Nouvelle classe")
            self.assertEqual(
                reader.execute("SELECT COUNT(*) FROM classrooms").fetchone()[0], 0
            )
        self.assertEqual(len(self.store.classrooms(self.admin)), 1)

    def test_ollama_rejects_malformed_vectors(self):
        from app.knowledge import embeddings

        for invalid in (None, [None], [[0, 0]], [[True]], [[float("nan")]]):
            with patch(
                "app.knowledge._ollama_json", return_value={"embeddings": invalid}
            ):
                with self.assertRaises(ValueError):
                    embeddings(["Une question"])

    def test_classroom_invitations_isolate_documents(self):
        self.store.create_classroom(self.admin, "L1 Alpha")
        self.store.create_classroom(self.admin, "L1 Beta")
        alpha, beta = [row["id"] for row in self.store.classrooms(self.admin)]
        code = self.store.issue_invitation(self.admin, "student", alpha)
        with self.assertRaises(ValueError):
            self.store.register_invited("wrong-role", PASSWORD, "admin", code)
        learner, _ = self.store.register_invited(
            "alpha-student", PASSWORD, "student", code
        )
        self.store.join_classroom(self.student, code)
        teacher_code = self.store.issue_invitation(self.admin, "manager", alpha)
        self.store.join_classroom(self.manager, teacher_code)
        with self.assertRaises(PermissionError):
            self.store.issue_invitation(self.manager, "student", beta)
        with self.assertRaises(ValueError):
            self.store.register_invited(
                "other-teacher", PASSWORD, "manager", teacher_code
            )
        independent, _ = self.store.register_admin("new-admin", PASSWORD)
        with self.assertRaises(ValueError):
            self.store.issue_invitation(self.admin, "admin")
        with self.assertRaises(PermissionError):
            self.store.update_user(independent, self.admin, "student", False)

        for classroom, count in [(alpha, 8), (beta, 16)]:
            name = f"stage-{classroom}.txt"
            blob = f"La durée minimale du stage est de {count} semaines.".encode()
            version = self.store.add_versions(
                self.admin,
                [(name, blob, parse_document(name, blob))],
                "Procédure",
                "École",
                START,
                END,
                "students",
                ["Stages"],
                classroom=classroom,
            )[0]
            self.store.update_version(
                self.admin,
                version,
                "École",
                START,
                END,
                "students",
                ["Stages"],
                submit=True,
            )
            self.store.publish(self.admin, version)
        self.assertEqual(self.store.versions(independent), [])
        self.assertEqual(self.store.versions(independent, management=True), [])
        with self.assertRaises(ValueError):
            self.store.publish(independent, version)
        with self.assertRaises(PermissionError):
            self.store.version_preview(independent, version)
        with self.assertRaises(ValueError):
            self.store.issue_invitation(independent, "student", alpha)
        own_class = self.store.classrooms(independent)[0]["id"]
        own_code = self.store.issue_invitation(independent, "student", own_class)
        outsider, _ = self.store.register_invited(
            "other-student", PASSWORD, "student", own_code
        )
        self.assertEqual(self.store.versions(outsider), [])
        blob = "Le stage en laboratoire dure 20 semaines.".encode()
        own_document = self.store.add_versions(
            independent,
            [("laboratoire.txt", blob, parse_document("laboratoire.txt", blob))],
            "Procédure",
            "Autre école",
            START,
            END,
            "students",
            ["Stages"],
        )[0]
        self.store.update_version(
            independent,
            own_document,
            "Autre école",
            START,
            END,
            "students",
            ["Stages"],
            submit=True,
        )
        self.store.publish(independent, own_document)
        own_answer = answer_question(
            self.store,
            outsider,
            "Quelle est la durée du stage en laboratoire ?",
            classroom=own_class,
            semantic=False,
        )
        self.assertIn("20 semaines", own_answer.answer)
        self.assertNotIn(
            own_document, {row["id"] for row in self.store.versions(self.admin)}
        )
        self.assertEqual(
            [row["id"] for row in self.store.accounts(independent)],
            [independent, outsider],
        )
        self.assertEqual(self.store.review_queue(independent), [])
        self.assertEqual(self.store.requests(independent, staff=True), [])
        result = answer_question(
            self.store,
            learner,
            "Quelle est la durée du stage ?",
            classroom=alpha,
            semantic=False,
        )
        self.assertIn("8 semaines", result.answer)
        with self.assertRaises(PermissionError):
            self.store.version_preview(learner, version)
        with self.assertRaises(PermissionError):
            answer_question(
                self.store,
                learner,
                "Quelle est la durée du stage ?",
                classroom=beta,
                semantic=False,
            )

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "test.sqlite3", lambda: TODAY)
        self.store.initialize()
        self.admin = self.store.bootstrap("admin", PASSWORD)
        self.manager = self.store.create_user(
            self.admin, "gestion", PASSWORD, "manager"
        )
        self.student = self.store.create_user(self.admin, "etu", PASSWORD, "student")

    def add(
        self,
        name,
        text,
        domains,
        visibility="students",
        end=END,
        kind="Procédure",
        submit=True,
        publish=True,
        actor=None,
    ):
        actor = actor or self.admin
        blob = text.encode() if isinstance(text, str) else text
        v = self.store.add_versions(
            actor,
            [(name, blob, parse_document(name, blob))],
            kind,
            "Scolarité",
            START,
            end,
            visibility,
            domains,
        )[0]
        if submit:
            self.store.update_version(
                actor, v, "Scolarité", START, end, visibility, domains, submit=True
            )
        if publish:
            self.store.publish(self.admin, v)
        return v

    def ask(self, question, domain=None, **options):
        return answer_question(
            self.store, self.student, question, domain, semantic=False, **options
        )

    def test_identity_and_roles(self):
        self.assertIsNone(self.store.authenticate("gestion", "wrong"))
        user, revision = self.store.authenticate("GESTION", PASSWORD)
        self.assertEqual(user, self.manager)
        self.assertEqual(self.store.session_user(user, revision)["role"], "manager")
        with self.assertRaises(PermissionError):
            self.store.create_user(self.student, "intrus", PASSWORD, "admin")
        with self.assertRaises(PermissionError):
            self.add(
                "reg.txt",
                "Règlement de la commission",
                ["Examens"],
                kind="Règlement",
                actor=self.manager,
            )
        self.store.update_user(self.admin, user, "student", True)
        self.assertIsNone(self.store.session_user(user, revision))

    def test_pending_private_and_manager_validation(self):
        pending = self.add(
            "pending.txt",
            "Les dossiers de bourse ouvrent en avril.",
            ["Bourses"],
            publish=False,
            actor=self.manager,
        )
        with self.assertRaises(PermissionError):
            self.store.publish(self.manager, pending)
        private = self.add(
            "secret.txt",
            "Le dossier de bourse spécial contient le code AX-760.",
            ["Bourses"],
            visibility="admin",
        )
        self.assertEqual(
            self.ask("Quel code pour le dossier de bourse spécial ?", "Bourses").status,
            "unanswered",
        )
        with self.assertRaises(PermissionError):
            self.store.version_preview(self.student, private)
        with self.assertRaises(PermissionError):
            self.store.version_preview(self.manager, private, management=True)
        self.store.publish(self.admin, pending)
        self.assertIn(
            "avril",
            self.ask("Quand ouvrent les dossiers de bourse ?", "Bourses").answer,
        )

    def test_exact_answers_sources_typo_and_synonym(self):
        text = "RÈGLEMENT\nArticle 14 - Absences\nPour une absence médicale, déposez le justificatif sous 48 heures ouvrées."
        self.add("absences.txt", text, ["Absences"])
        for phrase in [
            "Quel est le délai pour un certificat d’absence médicale ?",
            "Quel justifictif fournir après une absence médicale ?",
        ]:
            answer = self.ask(phrase, "Absences")
            self.assertEqual(answer.status, "sourced")
            self.assertIn("48 heures ouvrées", answer.answer)
            self.assertEqual(answer.sources[0]["locator"], "Ligne 3 · Article 14")
            self.assertEqual(answer.sources[0]["filename"], "absences.txt")

    def test_no_context_and_multiple_questions(self):
        self.add(
            "absence.txt",
            "Le justificatif d’absence médicale est obligatoire sous 48 heures.",
            ["Absences"],
        )
        self.add(
            "stage.txt",
            "La durée minimale du stage de L2 est de 6 semaines.",
            ["Stages"],
        )
        self.add(
            "resto.txt",
            "Le restaurant universitaire est ouvert de 11h45 à 13h15.",
            ["Restauration"],
        )
        self.assertEqual(
            self.ask(
                "Quel est le menu du restaurant universitaire ?", "Restauration"
            ).status,
            "unanswered",
        )
        self.assertEqual(
            self.ask("Comment préparer une pizza au fromage ?").status, "unanswered"
        )
        outcome = self.ask(
            "Quel justificatif pour une absence médicale ? Quelle est la durée du stage de L2 ?"
        )
        self.assertEqual(outcome.status, "sourced")
        self.assertIn("48 heures", outcome.answer)
        self.assertIn("6 semaines", outcome.answer)
        self.assertEqual(
            {row["filename"] for row in outcome.sources}, {"absence.txt", "stage.txt"}
        )

    def test_pdf_citation_and_invalid_weekday(self):
        stream = BytesIO()
        pdf = canvas.Canvas(stream)
        pdf.drawString(72, 700, "Calendrier universitaire")
        pdf.showPage()
        pdf.drawString(72, 700, "Article 6")
        pdf.drawString(
            72,
            680,
            "La rentree pedagogique du semestre est fixee au lundi 8 septembre 2026.",
        )
        pdf.save()
        self.add("cal.pdf", stream.getvalue(), ["Emploi du temps"], kind="Calendrier")
        answer = self.ask(
            "Quand est fixée la rentrée pédagogique du semestre ?", "Emploi du temps"
        )
        self.assertEqual(answer.status, "conflict")
        self.assertEqual(answer.sources[0]["locator"], "Page 2 · Article 6")

    def test_contradictory_sources(self):
        for filename, days in [("a.txt", 3), ("b.txt", 5)]:
            self.add(
                filename,
                f"Le délai maximal pour déposer un justificatif d’absence médicale est de {days} jours.",
                ["Absences"],
            )
        answer = self.ask(
            "Quel délai pour déposer un justificatif d’absence médicale ?", "Absences"
        )
        self.assertEqual(answer.status, "conflict")
        self.assertEqual(len(answer.sources), 2)

    def test_expiration_retracts_answers_and_exports(self):
        self.add(
            "stage.txt",
            "La durée minimale du stage de L2 est de 6 semaines.",
            ["Stages"],
        )
        question = "Quelle est la durée minimale du stage de L2 ?"
        answer = self.ask(question, "Stages")
        conversation = self.store.save_turn(
            self.student,
            None,
            question,
            "Stages",
            "fr",
            answer.answer,
            answer.status,
            [row["id"] for row in answer.sources],
        )
        turn = self.store.conversation(self.student, conversation)[0]
        self.assertIn(
            "6 semaines", self.store.export_turn(self.student, turn["id"])["answer"]
        )
        self.store.clock = lambda: date(2027, 9, 1)
        self.assertEqual(self.ask(question, "Stages").status, "expired")
        self.assertIsNone(
            self.store.conversation(self.student, conversation)[0]["sources"]
        )
        with self.assertRaises(PermissionError):
            self.store.export_turn(self.student, turn["id"])

    def test_replacement_and_feedback_review(self):
        original = self.add(
            "initial.txt",
            "La durée minimale du stage de L2 est de 6 semaines.",
            ["Stages"],
        )
        question = "Quelle est la durée minimale du stage de L2 ?"
        answer = self.ask(question, "Stages")
        conversation = self.store.save_turn(
            self.student,
            None,
            question,
            "Stages",
            "fr",
            answer.answer,
            answer.status,
            [row["id"] for row in answer.sources],
        )
        turn = self.store.conversation(self.student, conversation)[0]["id"]
        with self.assertRaises(PermissionError):
            self.store.report(self.manager, turn, "Tentative sur un autre compte")
        self.store.vote(self.student, turn, True)
        self.store.report(self.student, turn, "Préciser la durée minimale.")
        self.store.propose_review(self.student, turn, "À vérifier")
        self.assertEqual(len(self.store.review_queue(self.admin)), 1)
        self.store.approve_review(
            self.admin, turn, "La durée minimale est de six semaines [1].", "approved"
        )
        self.assertEqual(self.ask(question, "Stages").status, "reviewed")
        content = "La durée minimale du stage de L2 est de 8 semaines.".encode()
        new = self.store.add_versions(
            self.admin,
            [("nouveau.txt", content, parse_document("nouveau.txt", content))],
            "Procédure",
            "Scolarité",
            START,
            END,
            "students",
            ["Stages"],
            replacement=original,
        )[0]
        self.assertIn(
            "6 semaines",
            self.ask(question, "Stages").answer.replace("six semaines", "6 semaines"),
        )
        self.store.update_version(
            self.admin,
            new,
            "Scolarité",
            START,
            END,
            "students",
            ["Stages"],
            submit=True,
        )
        self.store.publish(self.admin, new)
        self.assertIn("8 semaines", self.ask(question, "Stages").answer)
        self.assertIsNone(
            self.store.conversation(self.student, conversation)[0]["sources"]
        )

    def test_vector_lookup_and_export(self):
        self.add(
            "stage.txt", "Une immersion professionnelle dure sept semaines.", ["Stages"]
        )
        row = self.store.search_rows(self.admin, "Stages", EMBEDDING_MODEL)[0]
        self.store.save_vectors(self.admin, EMBEDDING_MODEL, [(row["id"], [1.0, 0.0])])
        with patch("app.knowledge.embeddings", return_value=[[1.0, 0.0]]):
            result = answer_question(
                self.store, self.student, "Internship length?", "Stages"
            )
        self.assertIn("sept semaines", result.answer)
        document = response_pdf(
            {
                "question": "Quelle durée ?",
                "answer": result.answer,
                "sources": [dict(result.sources[0], author="Scolarité")],
            }
        )
        pdf = PdfReader(BytesIO(document))
        self.assertIn("sept semaines", pdf.pages[0].extract_text())
        reminder = reminder_file(
            "Rendez-vous, scolarité", datetime(2026, 12, 1, 9), "Stages"
        )
        self.assertIn(b"BEGIN:VALARM", reminder)
        self.assertIn(b"DTSTART:20261201T090000", reminder)

    def test_request_routing_and_history_ownership(self):
        self.store.create_request(
            self.student, "Stages", "Je dois faire signer une convention de stage."
        )
        self.store.create_request(
            self.student,
            "Règlement disciplinaire",
            "Je voudrais consulter le règlement.",
        )
        manager_requests = self.store.requests(self.manager, staff=True)
        self.assertEqual(len(manager_requests), 1)
        self.store.handle_request(
            self.manager,
            manager_requests[0]["id"],
            "Traitée",
            "Veuillez contacter la scolarité.",
        )
        self.assertEqual(len(self.store.requests(self.student)), 2)
        self.assertEqual(len(self.store.requests(self.admin, staff=True)), 2)
        conversation = self.store.save_turn(
            self.student,
            None,
            "Première question",
            None,
            "fr",
            "Aucune source",
            "unanswered",
            [],
        )
        self.assertEqual(self.store.conversation(self.manager, conversation), [])
        self.assertEqual(len(self.store.conversations(self.student, "première")), 1)

    def test_folder_updates_and_notifications(self):
        def import_version(text):
            blob = text.encode()
            self.store.seed(
                [
                    (
                        "folder.txt",
                        "Procédure",
                        ["Stages"],
                        blob,
                        parse_document("folder.txt", blob),
                    )
                ]
            )

        import_version("Le stage de L2 dure six semaines.")
        original = self.store.versions(self.admin, management=True)[0]
        identifier = original["id"]
        self.store.update_version(
            self.admin,
            identifier,
            "École",
            START,
            END,
            "students",
            ["Stages"],
            submit=True,
        )
        self.store.publish(self.admin, identifier)
        self.assertEqual(self.store.unread_updates(self.student), 1)
        self.store.mark_updates_read(self.student)
        self.assertEqual(self.store.unread_updates(self.student), 0)
        import_version("Le stage de L2 dure sept semaines.")
        import_version("Le stage de L2 dure sept semaines.")
        versions = self.store.versions(self.admin, management=True)
        self.assertEqual(
            [(row["number"], row["status"]) for row in versions],
            [(2, "draft"), (1, "published")],
        )
        self.assertEqual(versions[0]["document_id"], original["document_id"])
        self.assertIn(
            "six semaines",
            self.ask("Quelle est la durée du stage de L2 ?", "Stages").answer,
        )


if __name__ == "__main__":
    unittest.main()
