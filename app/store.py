import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.appearance import SYSTEM_THEME, THEME_LABELS

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("GUIDEBOT_DB", ROOT / "data" / "guidebot.sqlite3"))
ROLES = {
    "student": "Étudiant",
    "manager": "Gestionnaire pédagogique",
    "admin": "Administrateur",
}
VISIBILITY = {
    "students": "Communauté étudiante",
    "staff": "Équipe pédagogique",
    "admin": "Administrateurs",
}
ACCESS = {
    "student": ("students",),
    "manager": ("students", "staff"),
    "admin": tuple(VISIBILITY),
}
KINDS = ("Procédure", "Calendrier", "Règlement", "Formulaire", "Autre")
STATES = {
    "draft": "Brouillon",
    "pending": "À valider",
    "published": "Validée",
    "archived": "Archivée",
}
DOMAINS = (
    "Absences",
    "Examens",
    "Stages",
    "Emploi du temps",
    "Inscriptions",
    "Bourses",
    "Restauration",
    "Activités",
    "Contacts",
    "Règlement disciplinaire",
)
PASSWORD_ROUNDS = 600_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('student','manager','admin')),
    active INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS appearance_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    theme TEXT NOT NULL CHECK(theme IN ('light','dark','forest','contrast'))
);
CREATE TABLE IF NOT EXISTS pending_onboarding (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS login_limits (
    username TEXT PRIMARY KEY, failures INTEGER NOT NULL, retry_after TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS classrooms (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS classroom_members (
    classroom_id INTEGER REFERENCES classrooms(id), user_id INTEGER REFERENCES users(id),
    PRIMARY KEY(classroom_id,user_id)
);
CREATE TABLE IF NOT EXISTS classroom_documents (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id), classroom_id INTEGER NOT NULL REFERENCES classrooms(id)
);
CREATE TABLE IF NOT EXISTS invitations (
    digest TEXT PRIMARY KEY, role TEXT NOT NULL, classroom_id INTEGER REFERENCES classrooms(id),
    issuer INTEGER NOT NULL REFERENCES users(id), expires TEXT NOT NULL, remaining INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
    number INTEGER NOT NULL, filename TEXT NOT NULL, content BLOB NOT NULL,
    author TEXT NOT NULL DEFAULT '', publication TEXT, expiration TEXT,
    visibility TEXT NOT NULL DEFAULT 'students', status TEXT NOT NULL DEFAULT 'draft',
    created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
    validated_by INTEGER REFERENCES users(id), validated_at TEXT,
    UNIQUE(document_id, number)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_published_version
    ON versions(document_id) WHERE status = 'published';
CREATE TABLE IF NOT EXISTS version_domains (
    version_id INTEGER NOT NULL REFERENCES versions(id), domain TEXT NOT NULL,
    PRIMARY KEY(version_id, domain)
);
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES versions(id),
    locator TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vectors (
    passage_id INTEGER REFERENCES passages(id), model TEXT NOT NULL, vector TEXT NOT NULL,
    PRIMARY KEY(passage_id, model)
);
CREATE TABLE IF NOT EXISTS folder_sources (
    filename TEXT PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id), fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS updates_seen (user_id INTEGER PRIMARY KEY REFERENCES users(id), seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY, owner INTEGER REFERENCES users(id),
    title TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    question TEXT NOT NULL, domain TEXT, answer TEXT NOT NULL, result TEXT NOT NULL,
    created_at TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'fr'
);
CREATE INDEX IF NOT EXISTS turns_by_conversation ON turns(conversation_id, id);
CREATE TABLE IF NOT EXISTS turn_sources (
    turn_id INTEGER NOT NULL REFERENCES turns(id), passage_id INTEGER NOT NULL REFERENCES passages(id),
    position INTEGER NOT NULL, PRIMARY KEY(turn_id, passage_id)
);
CREATE TABLE IF NOT EXISTS turn_interpretations (
    turn_id INTEGER PRIMARY KEY REFERENCES turns(id), explanation TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turn_research (
    turn_id INTEGER PRIMARY KEY REFERENCES turns(id),
    scope TEXT NOT NULL CHECK(scope IN ('school','web','both')),
    web_state TEXT NOT NULL,
    web_queries INTEGER NOT NULL,
    web_sources TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    turn_id INTEGER PRIMARY KEY REFERENCES turns(id), useful INTEGER,
    report TEXT, reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY, turn_id INTEGER NOT NULL REFERENCES turns(id), answer TEXT NOT NULL,
    state TEXT NOT NULL, reviewer INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY, owner INTEGER NOT NULL REFERENCES users(id), domain TEXT NOT NULL,
    message TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Reçue',
    response TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS domain_links (
    domain TEXT PRIMARY KEY, procedure_url TEXT NOT NULL DEFAULT '', contact TEXT NOT NULL DEFAULT '',
    calendar_url TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, actor INTEGER REFERENCES users(id), action TEXT NOT NULL,
    item TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS legacy_imports (fingerprint TEXT PRIMARY KEY);
"""


def now():
    return datetime.now(timezone.utc)


def audit(db, actor, action, item):
    db.execute(
        "INSERT INTO audit(actor, action, item, created_at) VALUES (?, ?, ?, ?)",
        (actor, action, str(item), now().isoformat()),
    )


def normalize_username(username):
    return username.strip().casefold()


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ROUNDS)
    return f"{salt.hex()}:{digest.hex()}"


def password_matches(password, saved):
    salt, digest = saved.split(":")
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS
    )
    return hmac.compare_digest(actual.hex(), digest)


def validate_account(username, password):
    username = normalize_username(username)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._@-]{2,79}", username):
        raise ValueError(
            "Utilisez un identifiant de 3 à 80 caractères (lettres, chiffres, point, tiret ou @)."
        )
    if not 12 <= len(password) <= 128:
        raise ValueError("Le mot de passe doit contenir entre 12 et 128 caractères.")
    return username


class Store:
    def __init__(self, path=DB_PATH, clock=date.today):
        self.path = Path(path)
        self.clock = clock

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=12)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=12)
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(SCHEMA)
        finally:
            db.close()
        with self.connect(write=True) as db:
            for table in ("users", "documents", "classrooms", "conversations"):
                columns = {
                    row["name"] for row in db.execute(f"PRAGMA table_info({table})")
                }
                if "workspace" not in columns:
                    db.execute(
                        f"ALTER TABLE {table} ADD COLUMN workspace INTEGER NOT NULL DEFAULT 0"
                    )
                if table == "classrooms" and "display_name" not in columns:
                    db.execute("ALTER TABLE classrooms ADD COLUMN display_name TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS workspace_links (
                workspace INTEGER NOT NULL, domain TEXT NOT NULL, procedure_url TEXT NOT NULL,
                contact TEXT NOT NULL, calendar_url TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(workspace,domain))""")
            db.execute("""INSERT OR IGNORE INTO workspace_links SELECT 0,domain,procedure_url,
                contact,calendar_url,updated_at FROM domain_links""")
            for statement in (
                "CREATE INDEX IF NOT EXISTS users_by_workspace ON users(workspace,username)",
                "CREATE INDEX IF NOT EXISTS documents_by_workspace ON documents(workspace)",
                "CREATE INDEX IF NOT EXISTS conversations_by_owner ON conversations(owner,updated_at)",
                "CREATE INDEX IF NOT EXISTS conversations_by_workspace ON conversations(workspace)",
                "CREATE INDEX IF NOT EXISTS reviews_by_turn ON reviews(turn_id,id)",
                "CREATE INDEX IF NOT EXISTS passages_by_version ON passages(version_id)",
            ):
                db.execute(statement)
            db.execute("""INSERT INTO classrooms(name,workspace,display_name)
                SELECT '0:default',0,'Mes étudiants'
                WHERE EXISTS (SELECT 1 FROM users WHERE workspace=0 AND role='admin')
                AND NOT EXISTS (SELECT 1 FROM classrooms WHERE workspace=0)""")

    def today(self):
        return self.clock().isoformat()

    def needs_setup(self):
        with self.connect() as db:
            return not db.execute("SELECT 1 FROM users LIMIT 1").fetchone()

    def bootstrap(self, username, password):
        username = validate_account(username, password)
        hashed = password_hash(password)
        with self.connect(write=True) as db:
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("Le premier compte a déjà été créé.")
            return self._create_user(db, username, hashed, "admin", None)

    def register_admin(self, username, password):
        username = validate_account(username, password)
        hashed = password_hash(password)
        with self.connect(write=True) as db:
            identifier = self._create_user(db, username, hashed, "admin", None)
            db.execute(
                "UPDATE users SET workspace=? WHERE id=?", (identifier, identifier)
            )
            db.execute(
                "INSERT INTO classrooms(name,workspace,display_name) VALUES (?,?,?)",
                (f"{identifier}:default", identifier, "Mes étudiants"),
            )
            return identifier, 0

    def _create_user(self, db, username, hashed, role, actor, workspace=None):
        if role not in ROLES:
            raise ValueError("Rôle invalide.")
        if workspace is None:
            workspace = self._actor(db, actor)["workspace"] if actor else 0
        try:
            user_id = db.execute(
                "INSERT INTO users(username, password, role, workspace) VALUES (?, ?, ?, ?)",
                (username, hashed, role, workspace),
            ).lastrowid
        except sqlite3.IntegrityError as error:
            raise ValueError("Cet identifiant existe déjà.") from error
        db.execute("INSERT INTO pending_onboarding(user_id) VALUES (?)", (user_id,))
        audit(db, actor or user_id, "Compte créé", user_id)
        return user_id

    def _actor(self, db, user_id, roles=None):
        user = db.execute(
            "SELECT id, username, role, revision, workspace FROM users WHERE id=? AND active=1",
            (user_id,),
        ).fetchone()
        if not user or (roles and user["role"] not in roles):
            raise PermissionError("Cette action n’est pas autorisée.")
        return user

    def appearance_preference(self, actor):
        with self.connect() as db:
            self._actor(db, actor)
            row = db.execute(
                "SELECT theme FROM appearance_preferences WHERE user_id=?",
                (actor,),
            ).fetchone()
            return row["theme"] if row else SYSTEM_THEME

    def needs_onboarding(self, actor):
        with self.connect() as db:
            self._actor(db, actor)
            return (
                db.execute(
                    "SELECT 1 FROM pending_onboarding WHERE user_id=?", (actor,)
                ).fetchone()
                is not None
            )

    def finish_onboarding(self, actor):
        with self.connect(write=True) as db:
            self._actor(db, actor)
            db.execute("DELETE FROM pending_onboarding WHERE user_id=?", (actor,))

    def save_appearance_preference(self, actor, theme):
        if theme not in THEME_LABELS:
            raise ValueError("Thème inconnu.")
        with self.connect(write=True) as db:
            self._actor(db, actor)
            if theme == SYSTEM_THEME:
                db.execute(
                    "DELETE FROM appearance_preferences WHERE user_id=?", (actor,)
                )
            else:
                db.execute(
                    """INSERT INTO appearance_preferences VALUES (?,?)
                       ON CONFLICT(user_id) DO UPDATE SET theme=excluded.theme""",
                    (actor, theme),
                )

    def session_user(self, user_id, revision):
        with self.connect() as db:
            try:
                user = self._actor(db, user_id)
            except PermissionError:
                return None
            return user if user["revision"] == revision else None

    def school_user(self, username):
        with self.connect() as db:
            return db.execute(
                "SELECT id,username,role,revision,workspace FROM users WHERE username=? AND active=1",
                (normalize_username(username),),
            ).fetchone()

    def authenticate(self, username, password):
        username = normalize_username(username)
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9._@-]{2,79}", username)
            or len(password) > 128
        ):
            return None
        with self.connect() as db:
            account = db.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
            limit = db.execute(
                "SELECT * FROM login_limits WHERE username=?", (username,)
            ).fetchone()
        stamp = now()
        if (
            limit
            and limit["retry_after"] > stamp.isoformat()
            and limit["failures"] >= 5
        ):
            return None
        dummy = "00" * 16 + ":" + "00" * 32
        valid = password_matches(password, account["password"] if account else dummy)
        if not account:
            return None
        with self.connect(write=True) as db:
            stamp = now()
            limit = db.execute(
                "SELECT failures,retry_after FROM login_limits WHERE username=?",
                (username,),
            ).fetchone()
            if (
                limit
                and limit["failures"] >= 5
                and limit["retry_after"] > stamp.isoformat()
            ):
                return None
            current = db.execute(
                "SELECT id, role, active, revision FROM users WHERE id=?",
                (account["id"],),
            ).fetchone()
            if (
                valid
                and current["active"]
                and current["revision"] == account["revision"]
            ):
                db.execute("DELETE FROM login_limits WHERE username=?", (username,))
                return current["id"], current["revision"]
            db.execute(
                """INSERT INTO login_limits(username, failures, retry_after) VALUES (?, 1, ?)
                   ON CONFLICT(username) DO UPDATE SET
                   failures=CASE WHEN login_limits.retry_after < ? THEN 1 ELSE login_limits.failures+1 END,
                   retry_after=excluded.retry_after""",
                (
                    username,
                    (stamp + timedelta(minutes=2)).isoformat(),
                    stamp.isoformat(),
                ),
            )
        return None

    def accounts(self, actor):
        with self.connect() as db:
            workspace = self._actor(db, actor, {"admin"})["workspace"]
            return db.execute(
                "SELECT id, username, role, active FROM users WHERE workspace=? ORDER BY username",
                (workspace,),
            ).fetchall()

    def create_user(self, actor, username, password, role):
        username = validate_account(username, password)
        hashed = password_hash(password)
        with self.connect(write=True) as db:
            self._actor(db, actor, {"admin"})
            if role == "admin":
                raise ValueError(
                    "Chaque administrateur crée son propre espace depuis l’accueil."
                )
            return self._create_user(db, username, hashed, role, actor)

    def update_user(self, actor, user_id, role, active, password=""):
        if role not in ROLES or (password and not 12 <= len(password) <= 128):
            raise ValueError("Rôle ou longueur du mot de passe invalide.")
        hashed = password_hash(password) if password else None
        with self.connect(write=True) as db:
            self._actor(db, actor, {"admin"})
            workspace = self._actor(db, actor)["workspace"]
            target = db.execute(
                "SELECT * FROM users WHERE id=? AND workspace=?", (user_id, workspace)
            ).fetchone()
            if role == "admin" and user_id != actor:
                raise PermissionError(
                    "Un compte administrateur possède son propre espace."
                )
            if not target:
                raise PermissionError("Compte introuvable dans votre espace.")
            if user_id == actor and (role != "admin" or not active):
                raise ValueError(
                    "Votre compte doit conserver son accès administrateur."
                )
            db.execute(
                """UPDATE users SET role=?, active=?, password=COALESCE(?, password),
                   revision=revision+1 WHERE id=?""",
                (role, int(active), hashed, user_id),
            )
            if hashed:
                db.execute(
                    "DELETE FROM login_limits WHERE username=?", (target["username"],)
                )
            audit(db, actor, "Compte modifié", user_id)

    def change_password(self, actor, current_password, new_password):
        if len(current_password) > 128 or not 12 <= len(new_password) <= 128:
            raise ValueError(
                "Le nouveau mot de passe doit contenir entre 12 et 128 caractères."
            )
        with self.connect() as db:
            self._actor(db, actor)
            user = db.execute(
                "SELECT password, revision FROM users WHERE id=?", (actor,)
            ).fetchone()
        if not password_matches(current_password, user["password"]):
            raise ValueError("Le mot de passe actuel est incorrect.")
        hashed = password_hash(new_password)
        with self.connect(write=True) as db:
            self._actor(db, actor)
            if not db.execute(
                "UPDATE users SET password=?, revision=revision+1 WHERE id=? AND revision=?",
                (hashed, actor, user["revision"]),
            ).rowcount:
                raise ValueError("Le compte a changé. Reconnectez-vous.")
            audit(db, actor, "Mot de passe changé", actor)
            return user["revision"] + 1

    def classrooms(self, actor):
        with self.connect() as db:
            user = self._actor(db, actor)
            return db.execute(
                """SELECT c.id,COALESCE(c.display_name,c.name) AS name FROM classrooms c
                   WHERE c.workspace=? AND (?='admin' OR EXISTS
                   (SELECT 1 FROM classroom_members m WHERE m.classroom_id=c.id AND m.user_id=?))
                   ORDER BY name""",
                (user["workspace"], user["role"], actor),
            ).fetchall()

    def create_classroom(self, actor, name):
        name = name.strip()
        if not 2 <= len(name) <= 80:
            raise ValueError("Le nom de la classe doit contenir de 2 à 80 caractères.")
        with self.connect(write=True) as db:
            self._actor(db, actor, {"admin"})
            workspace = self._actor(db, actor)["workspace"]
            if db.execute(
                "SELECT 1 FROM classrooms WHERE workspace=? AND COALESCE(display_name,name)=? COLLATE NOCASE",
                (workspace, name),
            ).fetchone():
                raise ValueError("Cette classe existe déjà.")
            identifier = db.execute(
                "INSERT INTO classrooms(name,workspace,display_name) VALUES (?,?,?)",
                (f"{workspace}:{secrets.token_hex(12)}", workspace, name),
            ).lastrowid
            audit(db, actor, "Classe créée", identifier)

    def issue_invitation(self, actor, role, classroom=None):
        code = secrets.token_urlsafe(18)
        digest = hashlib.sha256(code.encode()).hexdigest()
        with self.connect(write=True) as db:
            issuer = self._actor(db, actor, {"admin", "manager"})
            if role not in {"student", "manager"} or classroom is None:
                raise ValueError("Choisissez le rôle et, si nécessaire, une classe.")
            if (
                classroom is not None
                and not db.execute(
                    "SELECT 1 FROM classrooms WHERE id=? AND workspace=?",
                    (classroom, issuer["workspace"]),
                ).fetchone()
            ):
                raise ValueError("Classe introuvable.")
            if issuer["role"] != "admin" and (
                role != "student" or not self._classroom_access(db, actor, classroom)
            ):
                raise PermissionError(
                    "Vous pouvez inviter des étudiants dans vos classes."
                )
            db.execute(
                "INSERT INTO invitations VALUES (?,?,?,?,?,?)",
                (
                    digest,
                    role,
                    classroom,
                    actor,
                    (now() + timedelta(days=7)).isoformat(),
                    50 if role == "student" else 1,
                ),
            )
            audit(db, actor, "Invitation créée", classroom or role)
        return code

    def _classroom_access(self, db, actor, classroom):
        return bool(
            db.execute(
                "SELECT 1 FROM classroom_members WHERE classroom_id=? AND user_id=?",
                (classroom, actor),
            ).fetchone()
        )

    def _invitation(self, db, code, role):
        digest = hashlib.sha256(code.strip().encode()).hexdigest()
        row = db.execute(
            """SELECT i.*,c.workspace FROM invitations i JOIN users u ON i.issuer=u.id JOIN classrooms c ON c.id=i.classroom_id
               WHERE i.digest=? AND i.role=? AND i.expires>? AND i.remaining>0 AND u.active=1
               AND u.workspace=c.workspace AND (u.role='admin' OR (u.role='manager' AND i.role='student' AND EXISTS
                    (SELECT 1 FROM classroom_members m WHERE m.classroom_id=i.classroom_id AND m.user_id=u.id)))""",
            (digest, role, now().isoformat()),
        ).fetchone()
        if not row:
            raise ValueError("Code invalide, expiré ou déjà utilisé.")
        return row

    def register_invited(self, username, password, role, code):
        username = validate_account(username, password)
        with self.connect() as db:
            self._invitation(db, code, role)
        hashed = password_hash(password)
        with self.connect(write=True) as db:
            invitation = self._invitation(db, code, role)
            user = self._create_user(db, username, hashed, role, invitation["issuer"])
            self._redeem(db, user, invitation)
            return user, 0

    def join_classroom(self, actor, code):
        with self.connect(write=True) as db:
            user = self._actor(db, actor, {"student", "manager"})
            invitation = self._invitation(db, code, user["role"])
            if user["workspace"] != invitation["workspace"]:
                raise PermissionError(
                    "Ce code appartient à un autre espace. Inscrivez-vous avec un compte dédié à cet espace."
                )
            self._redeem(db, actor, invitation)

    def _redeem(self, db, actor, invitation):
        classroom = invitation["classroom_id"]
        if classroom is not None:
            if self._classroom_access(db, actor, classroom):
                raise ValueError("Ce compte a déjà accès à cette classe.")
            db.execute("INSERT INTO classroom_members VALUES (?,?)", (classroom, actor))
        db.execute(
            "UPDATE invitations SET remaining=remaining-1 WHERE digest=?",
            (invitation["digest"],),
        )
        audit(db, actor, "Invitation utilisée", classroom or invitation["role"])

    def _document_scope(self, role, actor, document):
        workspace = f"EXISTS (SELECT 1 FROM documents d_scope JOIN users u_scope ON u_scope.workspace=d_scope.workspace WHERE d_scope.id={document} AND u_scope.id=?)"
        if role == "admin":
            return workspace, [actor]
        return (
            workspace
            + f""" AND NOT EXISTS (SELECT 1 FROM classroom_documents cd WHERE cd.document_id={document}
                AND NOT EXISTS (SELECT 1 FROM classroom_members cm
                WHERE cm.classroom_id=cd.classroom_id AND cm.user_id=?))""",
            [actor, actor],
        )

    def _class_clause(self, role, actor, alias="v"):
        return self._document_scope(role, actor, f"{alias}.document_id")

    def _manage_version(self, db, actor, version_id):
        user = self._actor(db, actor, {"manager", "admin"})
        version = db.execute(
            "SELECT v.*, d.title, d.kind FROM versions v JOIN documents d ON d.id=v.document_id WHERE v.id=? AND d.workspace=?",
            (version_id, user["workspace"]),
        ).fetchone()
        if not version:
            raise ValueError("Version introuvable.")
        scope = db.execute(
            "SELECT classroom_id FROM classroom_documents WHERE document_id=?",
            (version["document_id"],),
        ).fetchone()
        if user["role"] == "manager" and (
            (not scope and version["kind"] not in {"Calendrier", "Procédure"})
            or version["visibility"] == "admin"
        ):
            raise PermissionError("Cette version est réservée à l’administration.")
        if (
            scope
            and user["role"] != "admin"
            and not self._classroom_access(db, actor, scope["classroom_id"])
        ):
            raise PermissionError("Cette version appartient à une autre classe.")
        return version

    def seed(self, entries):
        with self.connect(write=True) as db:
            for filename, kind, domains, content, passages in entries:
                fingerprint = hashlib.sha256(content).hexdigest()
                source = db.execute(
                    "SELECT * FROM folder_sources WHERE filename=?", (filename,)
                ).fetchone()
                if source and source["fingerprint"] == fingerprint:
                    continue
                earlier = db.execute(
                    """SELECT id,document_id,visibility,content FROM versions v WHERE filename=? AND created_by IS NULL AND EXISTS (SELECT 1 FROM documents d WHERE d.id=v.document_id AND d.workspace=0)
                       ORDER BY id DESC LIMIT 1""",
                    (filename,),
                ).fetchone()
                if earlier:
                    document_id = earlier["document_id"]
                    original = db.execute(
                        """SELECT id,visibility FROM versions WHERE document_id=?
                           ORDER BY (status='published') DESC,number DESC LIMIT 1""",
                        (document_id,),
                    ).fetchone()
                    visibility = original["visibility"]
                    domains = [
                        row[0]
                        for row in db.execute(
                            "SELECT domain FROM version_domains WHERE version_id=?",
                            (original["id"],),
                        )
                    ] or domains
                else:
                    title = Path(filename).stem.replace("_", " ").capitalize()
                    document_id = db.execute(
                        "INSERT INTO documents(title,kind) VALUES (?,?)", (title, kind)
                    ).lastrowid
                    visibility = "students"
                if (
                    not earlier
                    or hashlib.sha256(earlier["content"]).hexdigest() != fingerprint
                ):
                    self._insert_version(
                        db,
                        document_id,
                        filename,
                        content,
                        passages,
                        ("", None, None, visibility, domains),
                        None,
                    )
                db.execute(
                    """INSERT INTO folder_sources VALUES (?,?,?) ON CONFLICT(filename)
                       DO UPDATE SET fingerprint=excluded.fingerprint""",
                    (filename, document_id, fingerprint),
                )

    def _metadata(self, author, publication, expiration, visibility, domains):
        author = author.strip()
        if not author or len(author) > 160 or not publication or not expiration:
            raise ValueError(
                "Renseignez un auteur, une date de publication et une date d’expiration."
            )
        if date.fromisoformat(expiration) < date.fromisoformat(publication):
            raise ValueError("La date d’expiration précède la publication.")
        if (
            visibility not in VISIBILITY
            or not domains
            or any(domain not in DOMAINS for domain in domains)
        ):
            raise ValueError(
                "Choisissez un niveau de confidentialité et au moins un domaine."
            )
        return author, publication, expiration, visibility, list(dict.fromkeys(domains))

    def _insert_version(
        self, db, document_id, filename, content, passages, metadata, actor
    ):
        author, publication, expiration, visibility, domains = metadata
        number = db.execute(
            "SELECT COALESCE(MAX(number),0)+1 FROM versions WHERE document_id=?",
            (document_id,),
        ).fetchone()[0]
        version_id = db.execute(
            """INSERT INTO versions(document_id,number,filename,content,author,publication,expiration,
               visibility,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                document_id,
                number,
                filename,
                content,
                author,
                publication,
                expiration,
                visibility,
                actor,
                now().isoformat(),
            ),
        ).lastrowid
        self._set_domains(db, version_id, domains)
        db.executemany(
            "INSERT INTO passages(version_id,locator,text) VALUES (?,?,?)",
            ((version_id, locator, text) for locator, text in passages),
        )
        audit(db, actor, "Version ajoutée", version_id)
        return version_id

    def _set_domains(self, db, version_id, domains):
        db.execute("DELETE FROM version_domains WHERE version_id=?", (version_id,))
        db.executemany(
            "INSERT INTO version_domains VALUES (?,?)",
            ((version_id, d) for d in domains),
        )

    def add_versions(
        self,
        actor,
        files,
        kind,
        author,
        publication,
        expiration,
        visibility,
        domains,
        replacement=None,
        classroom=None,
    ):
        metadata = self._metadata(author, publication, expiration, visibility, domains)
        if not files or kind not in KINDS or (replacement and len(files) != 1):
            raise ValueError(
                "Sélectionnez des fichiers valides. Un remplacement accepte un fichier."
            )
        with self.connect(write=True) as db:
            user = self._actor(db, actor, {"manager", "admin"})
            original = (
                self._manage_version(db, actor, replacement) if replacement else None
            )
            if original:
                kind = original["kind"]
                classroom = None
            if classroom is not None:
                if not db.execute(
                    "SELECT 1 FROM classrooms WHERE id=? AND workspace=?",
                    (classroom, user["workspace"]),
                ).fetchone():
                    raise ValueError("Classe introuvable.")
                if user["role"] != "admin" and not self._classroom_access(
                    db, actor, classroom
                ):
                    raise PermissionError("Vous ne gérez pas cette classe.")
            if user["role"] == "manager" and (
                (
                    not original
                    and classroom is None
                    and kind not in {"Calendrier", "Procédure"}
                )
                or visibility == "admin"
            ):
                raise PermissionError(
                    "Choisissez une classe pour déposer ce type de document."
                )
            ids = []
            for filename, content, passages in files:
                document_id = (
                    original["document_id"]
                    if original
                    else db.execute(
                        "INSERT INTO documents(title,kind,workspace) VALUES (?,?,?)",
                        (
                            Path(filename).stem.replace("_", " ").capitalize(),
                            kind,
                            user["workspace"],
                        ),
                    ).lastrowid
                )
                if classroom is not None:
                    db.execute(
                        "INSERT INTO classroom_documents VALUES (?,?)",
                        (document_id, classroom),
                    )
                ids.append(
                    self._insert_version(
                        db, document_id, filename, content, passages, metadata, actor
                    )
                )
            return ids

    def update_version(
        self,
        actor,
        version_id,
        author,
        publication,
        expiration,
        visibility,
        domains,
        submit=False,
    ):
        author, publication, expiration, visibility, domains = self._metadata(
            author, publication, expiration, visibility, domains
        )
        with self.connect(write=True) as db:
            version = self._manage_version(db, actor, version_id)
            user = self._actor(db, actor)
            if version["status"] not in {"draft", "pending"} or (
                user["role"] != "admin" and visibility == "admin"
            ):
                raise PermissionError("Cette version ne peut plus être modifiée.")
            if submit and expiration < self.today():
                raise ValueError("Une version expirée ne peut pas être soumise.")
            if (
                submit
                and not db.execute(
                    "SELECT 1 FROM passages WHERE version_id=?", (version_id,)
                ).fetchone()
            ):
                raise ValueError("Ce document ne contient aucun passage lisible.")
            status = "pending" if submit else "draft"
            db.execute(
                "UPDATE versions SET author=?,publication=?,expiration=?,visibility=?,status=? WHERE id=?",
                (author, publication, expiration, visibility, status, version_id),
            )
            self._set_domains(db, version_id, domains)
            audit(
                db,
                actor,
                "Version soumise" if submit else "Métadonnées modifiées",
                version_id,
            )

    def publish(self, actor, version_id):
        with self.connect(write=True) as db:
            self._actor(db, actor, {"admin"})
            version = self._manage_version(db, actor, version_id)
            if (
                version["status"] != "pending"
                or not version["publication"] <= self.today() <= version["expiration"]
            ):
                raise ValueError(
                    "La version doit être soumise et dans sa période de validité."
                )
            old = db.execute(
                "SELECT id FROM versions WHERE document_id=? AND status='published'",
                (version["document_id"],),
            ).fetchone()
            if old:
                db.execute(
                    "UPDATE versions SET status='archived' WHERE id=?", (old["id"],)
                )
                audit(db, actor, "Version remplacée", old["id"])
            db.execute(
                "UPDATE versions SET status='published',validated_by=?,validated_at=? WHERE id=?",
                (actor, now().isoformat(), version_id),
            )
            audit(db, actor, "Version validée", version_id)

    def archive(self, actor, version_id):
        with self.connect(write=True) as db:
            self._actor(db, actor, {"admin"})
            self._manage_version(db, actor, version_id)
            db.execute(
                "UPDATE versions SET status='archived' WHERE id=?", (version_id,)
            )
            audit(db, actor, "Version archivée", version_id)

    def _valid_clause(self, role, actor, alias="v"):
        scope, params = self._class_clause(role, actor, alias)
        visibility = ACCESS[role]
        placeholders = ",".join("?" for _ in visibility)
        return (
            f"{alias}.status='published' AND {alias}.publication<=? AND {alias}.expiration>=? AND {alias}.visibility IN ({placeholders}) AND ({scope})",
            [self.today(), self.today(), *visibility, *params],
        )

    def versions(self, actor, management=False):
        with self.connect() as db:
            user = self._actor(db, actor, {"manager", "admin"} if management else None)
            if management:
                condition = (
                    "1"
                    if user["role"] == "admin"
                    else "v.visibility!='admin' AND (d.kind IN ('Calendrier','Procédure') OR EXISTS (SELECT 1 FROM classroom_documents cd WHERE cd.document_id=v.document_id))"
                )
                scope, args = self._class_clause(user["role"], actor)
                condition += f" AND ({scope})"
            else:
                condition, args = self._valid_clause(user["role"], actor)
            return db.execute(
                f"""SELECT v.id,v.filename,v.number,v.status,v.author,v.expiration,v.publication,v.visibility,
                    v.validated_at,v.document_id,d.title,d.kind,u.username AS validator,
                    (SELECT c.name FROM classroom_documents cd JOIN classrooms c ON c.id=cd.classroom_id WHERE cd.document_id=d.id) AS classroom,
                    (SELECT GROUP_CONCAT(x.domain, '|') FROM version_domains x WHERE x.version_id=v.id) AS domains
                    FROM versions v JOIN documents d ON d.id=v.document_id
                    LEFT JOIN users u ON v.validated_by=u.id WHERE {condition}
                    ORDER BY d.title,v.number DESC""",
                args,
            ).fetchall()

    def unread_updates(self, actor):
        with self.connect() as db:
            role = self._actor(db, actor)["role"]
            condition, args = self._valid_clause(role, actor)
            return db.execute(
                f"""SELECT COUNT(*) FROM versions v WHERE {condition} AND
                    v.validated_at > COALESCE((SELECT seen_at FROM updates_seen WHERE user_id=?),'')""",
                (*args, actor),
            ).fetchone()[0]

    def mark_updates_read(self, actor):
        with self.connect(write=True) as db:
            self._actor(db, actor)
            db.execute(
                "INSERT OR REPLACE INTO updates_seen VALUES (?,?)",
                (actor, now().isoformat()),
            )

    def version_preview(self, actor, version_id, management=False):
        with self.connect() as db:
            user = self._actor(db, actor)
            if management:
                version = self._manage_version(db, actor, version_id)
            else:
                condition, args = self._valid_clause(user["role"], actor)
                version = db.execute(
                    f"SELECT v.* FROM versions v WHERE {condition} AND v.id=?",
                    (*args, version_id),
                ).fetchone()
            if not version:
                raise PermissionError("Le document n’est plus disponible.")
            passages = db.execute(
                "SELECT locator,text FROM passages WHERE version_id=? ORDER BY id",
                (version_id,),
            ).fetchall()
            return version["content"], passages

    def search_rows(self, actor, domain, model, expired=False, classroom=None):
        with self.connect() as db:
            user = self._actor(db, actor)
            where, args = self._valid_clause(user["role"], actor)
            if classroom is not None:
                if not db.execute(
                    "SELECT 1 FROM classrooms WHERE id=? AND workspace=?",
                    (classroom, user["workspace"]),
                ).fetchone() or (
                    user["role"] != "admin"
                    and not self._classroom_access(db, actor, classroom)
                ):
                    raise PermissionError("Vous ne faites pas partie de cette classe.")
                where += " AND NOT EXISTS (SELECT 1 FROM classroom_documents cd WHERE cd.document_id=v.document_id AND cd.classroom_id!=?)"
                args.append(classroom)
            if expired:
                where = where.replace("v.expiration>=?", "v.expiration<?")
            if domain:
                where += " AND EXISTS (SELECT 1 FROM version_domains x WHERE x.version_id=v.id AND x.domain=?)"
                args.append(domain)
            return db.execute(
                f"""SELECT p.id,p.text,p.locator,v.filename,v.publication,v.expiration,v.number,
                    d.kind,v.id AS version_id,v.document_id,e.vector FROM passages p
                    JOIN versions v ON p.version_id=v.id JOIN documents d ON d.id=v.document_id
                    LEFT JOIN vectors e ON e.passage_id=p.id AND e.model=? WHERE {where} ORDER BY p.id""",
                (model, *args),
            ).fetchall()

    def missing_vectors(self, actor, model, limit=48):
        with self.connect() as db:
            self._actor(db, actor, {"admin"})
            scope, args = self._class_clause("admin", actor)
            return db.execute(
                f"""SELECT p.id,p.text FROM passages p JOIN versions v ON v.id=p.version_id
                   WHERE v.status IN ('published','pending') AND ({scope})
                   AND NOT EXISTS (SELECT 1 FROM vectors e WHERE e.passage_id=p.id AND e.model=?)
                   ORDER BY p.id LIMIT ?""",
                (*args, model, limit),
            ).fetchall()

    def save_vectors(self, actor, model, rows):
        with self.connect(write=True) as db:
            self._actor(db, actor, {"admin"})
            scope, args = self._class_clause("admin", actor)
            db.executemany(
                f"""INSERT OR REPLACE INTO vectors SELECT ?,?,? WHERE EXISTS (
                   SELECT 1 FROM passages p JOIN versions v ON v.id=p.version_id WHERE p.id=? AND ({scope}))""",
                (
                    (
                        identifier,
                        model,
                        json.dumps(vector, allow_nan=False),
                        identifier,
                        *args,
                    )
                    for identifier, vector in rows
                ),
            )

    def conversations(self, actor, term=""):
        with self.connect() as db:
            self._actor(db, actor)
            rows = db.execute(
                """SELECT c.id,c.title,c.updated_at,t.question FROM conversations c
                   LEFT JOIN turns t ON t.conversation_id=c.id WHERE c.owner=? ORDER BY c.updated_at DESC,t.id""",
                (actor,),
            ).fetchall()
        match = term.strip().casefold()
        return list(
            {
                row["id"]: row
                for row in rows
                if match in row["title"].casefold()
                or match in (row["question"] or "").casefold()
            }.values()
        )

    def _own_turn(self, db, actor, turn):
        self._actor(db, actor)
        if not db.execute(
            "SELECT 1 FROM turns t JOIN conversations c ON c.id=t.conversation_id WHERE t.id=? AND c.owner=?",
            (turn, actor),
        ).fetchone():
            raise PermissionError("Cette réponse ne vous appartient pas.")

    def forget_conversation(self, actor, conversation):
        with self.connect(write=True) as db:
            self._actor(db, actor)
            if not db.execute(
                "UPDATE conversations SET owner=NULL WHERE id=? AND owner=?",
                (conversation, actor),
            ).rowcount:
                raise PermissionError("Cette discussion ne vous appartient pas.")

    def import_history(self, actor, username, archives):
        count = 0
        with self.connect(write=True) as db:
            self._actor(db, actor, {"admin"})
            owner = db.execute(
                "SELECT id FROM users WHERE username=? AND active=1 AND workspace=?",
                (normalize_username(username), self._actor(db, actor)["workspace"]),
            ).fetchone()
            if not owner:
                raise ValueError("Le compte destinataire doit exister et être actif.")
            for fingerprint, title, turns in archives:
                if not db.execute(
                    "INSERT OR IGNORE INTO legacy_imports VALUES (?)", (fingerprint,)
                ).rowcount:
                    continue
                stamp = now().isoformat()
                identifier = db.execute(
                    "INSERT INTO conversations(owner,title,updated_at,workspace) VALUES (?,?,?,?)",
                    (owner["id"], title, stamp, self._actor(db, actor)["workspace"]),
                ).lastrowid
                db.executemany(
                    "INSERT INTO turns(conversation_id,question,answer,result,created_at) VALUES (?,?,?,'legacy',?)",
                    (
                        (identifier, question, answer, stamp)
                        for question, answer in turns
                    ),
                )
                audit(db, actor, "Ancienne discussion importée", identifier)
                count += 1
        return count

    def _sources(self, db, role, turn, actor):
        condition, args = self._valid_clause(role, actor)
        total = db.execute(
            "SELECT COUNT(*) FROM turn_sources WHERE turn_id=?", (turn,)
        ).fetchone()[0]
        rows = db.execute(
            f"""SELECT p.id,p.locator,p.text,v.filename,v.number,v.id AS version_id,
                v.publication,v.expiration,v.author,d.kind FROM turn_sources s
                JOIN passages p ON p.id=s.passage_id JOIN versions v ON v.id=p.version_id
                JOIN documents d ON d.id=v.document_id WHERE s.turn_id=? AND {condition} ORDER BY s.position""",
            (turn, *args),
        ).fetchall()
        return rows if len(rows) == total else None

    def conversation(self, actor, conversation):
        if conversation is None:
            return []
        with self.connect() as db:
            user = self._actor(db, actor)
            rows = db.execute(
                """SELECT t.*,f.useful,f.report,i.explanation,r.scope,r.web_state,r.web_queries,r.web_sources AS web_sources_json
                   FROM turns t JOIN conversations c ON c.id=t.conversation_id
                   LEFT JOIN feedback f ON f.turn_id=t.id LEFT JOIN turn_interpretations i ON i.turn_id=t.id
                   LEFT JOIN turn_research r ON r.turn_id=t.id
                   WHERE c.id=? AND c.owner=? ORDER BY t.id""",
                (conversation, actor),
            ).fetchall()
            turns = []
            for row in rows:
                item = dict(row)
                item["web_sources"] = json.loads(item.pop("web_sources_json") or "[]")
                item["sources"] = self._sources(db, user["role"], row["id"], actor)
                if item["sources"] is None:
                    item["answer"] = ""
                    item["explanation"] = ""
                turns.append(item)
            return turns

    def export_turn(self, actor, turn):
        with self.connect() as db:
            self._own_turn(db, actor, turn)
            role = self._actor(db, actor)["role"]
            sources = self._sources(db, role, turn, actor)
            item = dict(
                db.execute(
                    """SELECT t.*,i.explanation,r.scope,r.web_state,r.web_queries,r.web_sources AS web_sources_json
                       FROM turns t LEFT JOIN turn_interpretations i ON i.turn_id=t.id
                       LEFT JOIN turn_research r ON r.turn_id=t.id WHERE t.id=?""",
                    (turn,),
                ).fetchone()
            )
            if sources is None or item["result"] == "legacy":
                raise PermissionError(
                    "La réponse et ses sources ne sont plus exportables."
                )
            web_sources = json.loads(item.pop("web_sources_json") or "[]")
            return dict(item, sources=sources, web_sources=web_sources)

    def save_explanation(self, actor, turn, text):
        with self.connect(write=True) as db:
            self._own_turn(db, actor, turn)
            role = self._actor(db, actor)["role"]
            sources = self._sources(db, role, turn, actor)
            research = db.execute(
                "SELECT web_sources FROM turn_research WHERE turn_id=?", (turn,)
            ).fetchone()
            public_sources = research and json.loads(research["web_sources"])
            if sources is None or not (sources or public_sources) or not text.strip():
                raise ValueError("Cette explication ne peut plus être enregistrée.")
            db.execute(
                "INSERT OR REPLACE INTO turn_interpretations VALUES (?,?,?)",
                (turn, text, now().isoformat()),
            )

    def save_turn(
        self,
        actor,
        conversation,
        question,
        domain,
        language,
        answer,
        result,
        passages,
        explanation=None,
        *,
        scope="school",
        web_state="not_requested",
        web_queries=0,
        web_sources=(),
    ):
        with self.connect(write=True) as db:
            user = self._actor(db, actor)
            stamp = now().isoformat()
            if conversation is None:
                conversation = db.execute(
                    "INSERT INTO conversations(owner,title,updated_at,workspace) VALUES (?,?,?,?)",
                    (actor, question[:70], stamp, user["workspace"]),
                ).lastrowid
            if not db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=? AND owner=?",
                (stamp, conversation, actor),
            ).rowcount:
                raise PermissionError("Cette discussion ne vous appartient pas.")
            turn = db.execute(
                """INSERT INTO turns(conversation_id,question,domain,answer,result,created_at,language)
                   VALUES (?,?,?,?,?,?,?)""",
                (conversation, question, domain, answer, result, stamp, language),
            ).lastrowid
            db.executemany(
                "INSERT INTO turn_sources(turn_id,passage_id,position) VALUES (?,?,?)",
                (
                    (turn, passage, index)
                    for index, passage in enumerate(dict.fromkeys(passages))
                ),
            )
            if passages and self._sources(db, user["role"], turn, actor) is None:
                raise ValueError(
                    "Un document a changé pendant la réponse. Relancez la question."
                )
            if scope != "school":
                db.execute(
                    "INSERT INTO turn_research(turn_id,scope,web_state,web_queries,web_sources) VALUES (?,?,?,?,?)",
                    (
                        turn,
                        scope,
                        web_state,
                        web_queries,
                        json.dumps(list(web_sources), ensure_ascii=False),
                    ),
                )
            if explanation and (passages or (scope != "school" and web_sources)):
                db.execute(
                    "INSERT INTO turn_interpretations VALUES (?,?,?)",
                    (turn, explanation, stamp),
                )
            return conversation

    def vote(self, actor, turn, useful):
        with self.connect(write=True) as db:
            self._own_turn(db, actor, turn)
            db.execute(
                """INSERT INTO feedback(turn_id,useful) VALUES (?,?)
                   ON CONFLICT(turn_id) DO UPDATE SET useful=excluded.useful""",
                (turn, int(useful)),
            )

    def report(self, actor, turn, report):
        report = report.strip()
        if not 4 <= len(report) <= 1000:
            raise ValueError("Décrivez le problème en 4 à 1 000 caractères.")
        with self.connect(write=True) as db:
            self._own_turn(db, actor, turn)
            db.execute(
                """INSERT INTO feedback(turn_id,report) VALUES (?,?)
                   ON CONFLICT(turn_id) DO UPDATE SET report=excluded.report, reviewed_by=NULL,reviewed_at=NULL""",
                (turn, report),
            )

    def propose_review(self, actor, turn, note):
        with self.connect(write=True) as db:
            self._own_turn(db, actor, turn)
            if db.execute(
                "SELECT 1 FROM turn_research WHERE turn_id=?", (turn,)
            ).fetchone():
                raise ValueError(
                    "Seules les réponses documentaires internes peuvent être soumises pour validation."
                )
            if db.execute(
                "SELECT 1 FROM reviews WHERE turn_id=? AND state='pending'", (turn,)
            ).fetchone():
                raise ValueError("Cette réponse est déjà en attente de validation.")
            db.execute(
                "INSERT INTO reviews(turn_id,answer,state,reviewer,created_at) VALUES (?,?,?,?,?)",
                (turn, note, "pending", actor, now().isoformat()),
            )
            audit(db, actor, "Réponse soumise", turn)

    def _check_review_scope(self, db, actor, turn):
        workspace = self._actor(db, actor, {"admin"})["workspace"]
        if not db.execute(
            """SELECT 1 FROM turns t JOIN conversations c ON c.id=t.conversation_id
                              WHERE t.id=? AND c.workspace=?""",
            (turn, workspace),
        ).fetchone():
            raise PermissionError("Cette réponse appartient à un autre espace.")

    def approve_review(self, actor, turn, answer, state):
        answer = answer.strip()
        if state not in {"approved", "rejected"} or (
            state == "approved" and not 1 <= len(answer) <= 1000
        ):
            raise ValueError("Renseignez une réponse validée de 1 à 1 000 caractères.")
        with self.connect(write=True) as db:
            self._check_review_scope(db, actor, turn)
            if (
                state == "approved"
                and db.execute(
                    "SELECT 1 FROM turn_research WHERE turn_id=?", (turn,)
                ).fetchone()
            ):
                raise ValueError(
                    "Une réponse utilisant le Web ne peut pas être publiée comme réponse officielle."
                )
            if state == "approved" and not self._sources(db, "admin", turn, actor):
                raise ValueError(
                    "Une réponse validée doit citer au moins une source actuelle."
                )
            db.execute(
                "UPDATE reviews SET state='processed' WHERE turn_id=? AND state='pending'",
                (turn,),
            )
            db.execute(
                "INSERT INTO reviews(turn_id,answer,state,reviewer,created_at) VALUES (?,?,?,?,?)",
                (turn, answer, state, actor, now().isoformat()),
            )
            db.execute(
                "UPDATE feedback SET reviewed_by=?,reviewed_at=? WHERE turn_id=?",
                (actor, now().isoformat(), turn),
            )
            audit(
                db,
                actor,
                "Réponse validée" if state == "approved" else "Signalement traité",
                turn,
            )

    def reviewed_answer(self, actor, question, domain, language):
        with self.connect() as db:
            user = self._actor(db, actor)
            options = db.execute(
                """SELECT r.answer,r.turn_id,r.state FROM reviews r JOIN turns t ON t.id=r.turn_id
                   WHERE EXISTS (SELECT 1 FROM conversations c WHERE c.id=t.conversation_id AND c.workspace=?) AND t.question=? COLLATE NOCASE AND t.domain IS ? AND t.language=?
                   AND r.state IN ('approved','rejected')
                   AND r.id=(SELECT MAX(x.id) FROM reviews x WHERE x.turn_id=r.turn_id
                             AND x.state IN ('approved','rejected'))
                   ORDER BY r.id DESC""",
                (user["workspace"], question, domain, language),
            ).fetchall()
            for row in options:
                if row["state"] == "rejected":
                    continue
                sources = self._sources(db, user["role"], row["turn_id"], actor)
                if sources:
                    return row["answer"], sources
        return None

    def review_queue(self, actor):
        with self.connect() as db:
            workspace = self._actor(db, actor, {"admin"})["workspace"]
            return db.execute(
                """SELECT t.id,t.question,t.answer,t.result,t.domain,t.created_at,f.report,f.useful,i.explanation,
                   (SELECT COUNT(DISTINCT c.owner) FROM turns x JOIN feedback fx ON fx.turn_id=x.id
                    JOIN conversations c ON c.id=x.conversation_id
                    WHERE c.workspace=base.workspace AND x.question=t.question COLLATE NOCASE AND x.domain IS t.domain
                    AND fx.report IS NOT NULL AND fx.reviewed_at IS NULL) AS reporter_count,
                   (SELECT x.answer FROM reviews x WHERE x.turn_id=t.id AND x.state='pending'
                    ORDER BY x.id DESC LIMIT 1) AS note
                   FROM turns t JOIN conversations base ON base.id=t.conversation_id LEFT JOIN feedback f ON f.turn_id=t.id
                   LEFT JOIN turn_interpretations i ON i.turn_id=t.id
                   WHERE base.workspace=? AND ((f.report IS NOT NULL AND f.reviewed_at IS NULL)
                   OR EXISTS (SELECT 1 FROM reviews x WHERE x.turn_id=t.id AND x.state='pending'))
               ORDER BY reporter_count DESC,t.created_at DESC""",
                (workspace,),
            ).fetchall()

    def review_sources(self, actor, turn):
        with self.connect() as db:
            self._check_review_scope(db, actor, turn)
            return self._sources(db, "admin", turn, actor) or []

    def review_history(self, actor):
        with self.connect() as db:
            workspace = self._actor(db, actor, {"admin"})["workspace"]
            return db.execute(
                """SELECT r.turn_id,r.answer,r.state,r.created_at,u.username FROM reviews r
                   JOIN users u ON r.reviewer=u.id JOIN turns t ON t.id=r.turn_id
                   JOIN conversations c ON c.id=t.conversation_id WHERE c.workspace=? ORDER BY r.id DESC LIMIT 100""",
                (workspace,),
            ).fetchall()

    def links(self, actor, domain):
        with self.connect() as db:
            self._actor(db, actor)
            return db.execute(
                "SELECT * FROM workspace_links WHERE workspace=? AND domain=?",
                (self._actor(db, actor)["workspace"], domain),
            ).fetchone()

    def set_links(self, actor, domain, procedure, contact, calendar):
        from urllib.parse import urlsplit

        def valid_url(url):
            parts = urlsplit(url)
            return not url or (
                parts.scheme == "https" and parts.hostname and not parts.username
            )

        if domain not in DOMAINS or not valid_url(procedure) or not valid_url(calendar):
            raise ValueError("Les liens doivent être des adresses HTTPS.")
        if contact and not re.fullmatch(
            r"[a-zA-Z0-9._+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", contact
        ):
            raise ValueError("Renseignez une adresse e-mail valide.")
        with self.connect(write=True) as db:
            self._actor(db, actor, {"admin"})
            db.execute(
                """INSERT INTO workspace_links VALUES (?,?,?,?,?,?) ON CONFLICT(workspace,domain) DO UPDATE SET
                   procedure_url=excluded.procedure_url,contact=excluded.contact,
                   calendar_url=excluded.calendar_url,updated_at=excluded.updated_at""",
                (
                    self._actor(db, actor)["workspace"],
                    domain,
                    procedure,
                    contact,
                    calendar,
                    now().isoformat(),
                ),
            )
            audit(db, actor, "Liens de domaine modifiés", domain)

    def create_request(self, actor, domain, message):
        message = message.strip()
        if domain not in DOMAINS or not 10 <= len(message) <= 2000:
            raise ValueError(
                "Sélectionnez un domaine et écrivez entre 10 et 2 000 caractères."
            )
        with self.connect(write=True) as db:
            self._actor(db, actor)
            request = db.execute(
                "INSERT INTO requests(owner,domain,message,created_at) VALUES (?,?,?,?)",
                (actor, domain, message, now().isoformat()),
            ).lastrowid
            audit(db, actor, "Demande déposée", request)

    def requests(self, actor, staff=False):
        with self.connect() as db:
            role = self._actor(db, actor, {"manager", "admin"} if staff else None)[
                "role"
            ]
            condition, args = (
                ("owner=?", (actor,))
                if not staff
                else ("1", ())
                if role == "admin"
                else (
                    "domain NOT IN ('Règlement disciplinaire','Contacts')",
                    (),
                )
            )
            condition = (
                f"({condition}) AND owner IN (SELECT id FROM users WHERE workspace=?)"
            )
            return db.execute(
                f"SELECT * FROM requests WHERE {condition} ORDER BY id DESC",
                (*args, self._actor(db, actor)["workspace"]),
            ).fetchall()

    def handle_request(self, actor, request, status, response):
        if status not in {"Reçue", "En cours", "Traitée"} or len(response) > 2000:
            raise ValueError("Statut ou réponse invalide.")
        with self.connect(write=True) as db:
            role = self._actor(db, actor, {"manager", "admin"})["role"]
            item = db.execute(
                "SELECT r.domain FROM requests r JOIN users u ON u.id=r.owner WHERE r.id=? AND u.workspace=?",
                (request, self._actor(db, actor)["workspace"]),
            ).fetchone()
            if not item or (
                role != "admin"
                and item["domain"] in {"Règlement disciplinaire", "Contacts"}
            ):
                raise PermissionError("Cette demande n’est pas accessible.")
            db.execute(
                "UPDATE requests SET status=?,response=? WHERE id=?",
                (status, response.strip(), request),
            )
            audit(db, actor, "Demande mise à jour", request)

    def metrics(self, actor, days):
        with self.connect() as db:
            user = self._actor(db, actor, {"manager", "admin"})
            role = user["role"]
            permitted = ACCESS[role]
            scope, scope_args = self._class_clause(role, actor)
            placeholders = ",".join("?" for _ in permitted)
            stamp = (now() - timedelta(days=days)).isoformat()
            db_turns = db.execute(
                f"""SELECT t.id,t.question,t.domain,t.result,t.created_at,f.useful,f.report,
                    (SELECT state FROM reviews r WHERE r.turn_id=t.id AND r.state IN ('approved','rejected')
                     ORDER BY r.id DESC LIMIT 1) AS review_state
                    FROM turns t JOIN conversations c ON c.id=t.conversation_id
                    JOIN users u ON c.owner=u.id LEFT JOIN feedback f ON f.turn_id=t.id
                    WHERE c.workspace=? AND t.created_at>=? AND t.result!='legacy' AND (?='admin' OR (u.role='student' AND EXISTS (
                        SELECT 1 FROM classroom_members learner JOIN classroom_members staff USING(classroom_id)
                        WHERE learner.user_id=u.id AND staff.user_id=?)))
                    AND (?='admin' OR (t.domain IS NOT 'Règlement disciplinaire' AND EXISTS (
                        SELECT 1 FROM turn_sources s JOIN passages p ON p.id=s.passage_id
                        JOIN versions source_version ON source_version.id=p.version_id
                        JOIN classroom_documents cd ON cd.document_id=source_version.document_id
                        JOIN classroom_members cm ON cm.classroom_id=cd.classroom_id
                        WHERE s.turn_id=t.id AND cm.user_id=?)))
                    AND NOT EXISTS (
                        SELECT 1 FROM turn_sources s JOIN passages p ON p.id=s.passage_id
                        JOIN versions v ON v.id=p.version_id WHERE s.turn_id=t.id
                        AND (v.visibility NOT IN ({placeholders}) OR NOT ({scope})))""",
                (
                    user["workspace"],
                    stamp,
                    role,
                    actor,
                    role,
                    actor,
                    *permitted,
                    *scope_args,
                ),
            ).fetchall()
            turn_ids = {turn["id"] for turn in db_turns}
            usage = {}
            for row in db.execute(
                """SELECT DISTINCT s.turn_id,v.document_id,v.id FROM turn_sources s
                   JOIN passages p ON p.id=s.passage_id JOIN versions v ON v.id=p.version_id
                   JOIN turns t ON t.id=s.turn_id JOIN conversations c ON c.id=t.conversation_id
                   WHERE c.workspace=? AND t.created_at>=?""",
                (user["workspace"], stamp),
            ):
                if row["turn_id"] in turn_ids:
                    identifier = row["id"]
                    usage[identifier] = usage.get(identifier, 0) + 1
            versions = db.execute(
                f"""SELECT id,filename,number,status,expiration FROM versions v
                    WHERE validated_at IS NOT NULL AND visibility IN ({",".join("?" for _ in permitted)}) AND ({scope})""",
                (*permitted, *scope_args),
            ).fetchall()
            return [dict(turn) for turn in db_turns], [
                dict(row, uses=usage.get(row["id"], 0)) for row in versions
            ]

    def audit_log(self, actor):
        with self.connect() as db:
            self._actor(db, actor, {"admin"})
            return db.execute(
                """SELECT a.action,a.item,a.created_at,u.username FROM audit a
                   LEFT JOIN users u ON a.actor=u.id WHERE u.workspace=? OR (a.actor IS NULL AND ?=0) ORDER BY a.id DESC LIMIT 100""",
                (self._actor(db, actor)["workspace"],) * 2,
            ).fetchall()
