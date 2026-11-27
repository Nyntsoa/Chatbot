import ipaddress
import os
import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import quote, urlsplit, urlunsplit
from xml.etree import ElementTree

import requests
from ddgs import DDGS
from ddgs.exceptions import DDGSException

from app.config import connected_mode
from app.knowledge import Result, answer_question, normalized, words

SCOPES = {
    "school": "Documents de l’établissement",
    "web": "Web public",
    "both": "Documents et Web public",
}
NEWS_QUERY = re.compile(
    r"\b(actualit[eé]s?|presse|journal|r[eé]cent\w*|derni[eè]r\w*)\b", re.I
)


def web_enabled():
    return connected_mode() and os.getenv("GUIDEBOT_WEB_SEARCH", "0") == "1"


@dataclass
class Research:
    result: Result
    scope: str
    web_sources: list = field(default_factory=list)
    web_state: str = "not_requested"
    web_queries: int = 0


def public_url(url):
    if not isinstance(url, str) or len(url) > 1500 or re.search(r"\s|\\", url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if (
            parts.scheme not in {"http", "https"}
            or not host
            or parts.username
            or parts.password
        ):
            return None
        host = host.rstrip(".").lower()
        if "." not in host or host.endswith((".localhost", ".local", ".internal")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return urlunsplit(parts._replace(fragment="")), host.removeprefix("www.")
    except ValueError:
        return None


def search_query(question):
    query = re.sub(r"[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}", " ", question)
    query = re.sub(r"(?:\+\d[\d .-]{7,}|\b\d(?:[ .-]?\d){8,}\b)", " ", query)
    query = re.sub(r"https?://\S+", " ", query)
    return " ".join(query.split())[:360]


def fallback_query(query):
    core = re.sub(
        r"^(?:(?:peux[- ]tu|pouvez[- ]vous|est[- ]ce que|je voudrais savoir|aide[- ]moi à)\s+)",
        "",
        query,
        flags=re.I,
    )
    core = re.sub(
        r"\b(?:s'il te plaît|s'il vous plaît|svp|stp)\b", "", core, flags=re.I
    )
    return core.strip(" ?!.")


def public_results(query, *, second=False):
    engine = DDGS(timeout=5)
    if second and NEWS_QUERY.search(query):
        try:
            return [
                dict(row, kind="press_extract")
                for row in engine.news(
                    query,
                    region="fr-fr",
                    safesearch="moderate",
                    max_results=6,
                    backend="duckduckgo,yahoo",
                )
            ]
        except DDGSException:
            return press_headlines(NEWS_QUERY.sub(" ", query).strip() or query)
    try:
        return engine.text(
            query,
            region="fr-fr" if second else "wt-wt",
            safesearch="moderate",
            max_results=5,
            backend="google" if not second else "google,mojeek",
        )
    except DDGSException:
        if not second:
            raise
        response = requests.get(
            "https://fr.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
            },
            headers={
                "User-Agent": "GuideBot/1.0 (assistant de recherche documentaire)"
            },
            timeout=5,
        )
        response.raise_for_status()
        return [
            {
                "title": row["title"],
                "href": f"https://fr.wikipedia.org/wiki/{quote(row['title'].replace(' ', '_'))}",
                "body": unescape(re.sub(r"<[^>]+>", "", row.get("snippet", ""))),
                "source": "Wikipédia",
            }
            for row in response.json().get("query", {}).get("search", [])[:5]
        ]


def press_headlines(query):
    response = requests.get(
        "https://news.google.com/rss/search",
        params={"q": query, "hl": "fr", "gl": "FR", "ceid": "FR:fr"},
        timeout=5,
    )
    response.raise_for_status()
    return [
        {
            "title": item.findtext("title"),
            "href": item.findtext("link"),
            "body": item.findtext("title"),
            "source": item.findtext("source"),
            "date": item.findtext("pubDate"),
            "kind": "press_headline",
        }
        for item in ElementTree.fromstring(response.content).findall("./channel/item")[
            :8
        ]
    ]


def search_public(question, *, deepen=False):
    if not web_enabled():
        return [], "disabled", 0
    query = search_query(question)
    tokens = words(query)
    if not tokens:
        return [], "empty", 0

    sources = {}
    attempts = successful = 0
    for second in (False, True):
        if (
            second
            and not deepen
            and not NEWS_QUERY.search(query)
            and len({source["host"] for source in sources.values()}) >= 2
        ):
            break
        current = fallback_query(query) if second else query
        attempts += 1
        try:
            rows = public_results(current or query, second=second)
        except (
            DDGSException,
            requests.RequestException,
            ValueError,
            ElementTree.ParseError,
        ):
            continue
        successful += 1
        for row in rows:
            parsed = public_url(row.get("href") or row.get("url"))
            title = " ".join(str(row.get("title") or "").split())[:200]
            snippet = " ".join(str(row.get("body") or "").split())[:520]
            if not parsed or not title or not snippet:
                continue
            url, host = parsed
            matches = tokens & words(f"{title} {snippet}")
            kind = row.get("kind", "web")
            if len(matches) < min(2, len(tokens)):
                continue
            sources.setdefault(
                url,
                {
                    "title": title,
                    "url": url,
                    "host": host,
                    "text": snippet,
                    "kind": kind,
                    "publisher": " ".join(str(row.get("source") or "").split())[:120],
                    "date": " ".join(str(row.get("date") or "").split())[:40],
                },
            )
    hits = sorted(
        sources.values(),
        key=lambda hit: (
            hit["kind"].startswith("press") and bool(NEWS_QUERY.search(query)),
            len(tokens & words(f"{hit['title']} {hit['text']}")),
        ),
        reverse=True,
    )[:4]
    return (
        hits,
        ("found" if hits else "empty" if successful else "unavailable"),
        attempts,
    )


def run_research(
    store,
    actor,
    question,
    scope,
    *,
    domain=None,
    turns=(),
    language="fr",
    classroom=None,
    deepen=False,
):
    if scope not in SCOPES:
        raise ValueError("Périmètre de recherche inconnu.")
    result = Result("", "unanswered")
    if scope in {"school", "both"}:
        result = answer_question(
            store,
            actor,
            question,
            domain,
            turns,
            language,
            classroom=classroom,
            deepen=deepen,
        )
    output = Research(result, scope)
    if scope == "school":
        return output
    output.web_sources, output.web_state, output.web_queries = search_public(
        question, deepen=deepen
    )
    if output.web_sources:
        offset = len(result.sources)
        excerpts = "\n\n".join(
            f"[{index}] {source['title']} · {source['publisher'] or source['host']}"
            + (
                " · titre de presse"
                if source["kind"] == "press_headline"
                else f"\n« {source['text']} »"
            )
            for index, source in enumerate(output.web_sources[:2], offset + 1)
        )
        if scope == "both":
            result.answer = f"Documents de l’établissement\n{result.answer}\n\nWeb public · extraits indexés\n{excerpts}"
        else:
            result.answer = excerpts
        if not result.sources:
            result.status = "web_sourced"
    elif scope == "web":
        result.answer = (
            "La recherche Web est indisponible pour le moment."
            if output.web_state in {"unavailable", "disabled"}
            else "Je n’ai pas trouvé de source publique suffisamment liée à cette question. Précisez le sujet pour que je puisse chercher autrement."
        )
    return output


SUGGESTED_FOLLOWUPS = {
    "Absences": (
        "À qui faut-il transmettre un justificatif d’absence ?",
        "Quels documents sont acceptés comme justificatifs ?",
    ),
    "Examens": (
        "Où consulter les convocations aux examens ?",
        "Comment se déroulent les rattrapages ?",
    ),
    "Stages": (
        "Comment faire valider ma convention de stage ?",
        "Quelles pièces faut-il préparer pour un stage ?",
    ),
    "Emploi du temps": (
        "Où consulter le calendrier pédagogique ?",
        "Comment vérifier la salle d’un cours ?",
    ),
}


def followup_questions(turn):
    if turn["result"] in {"unanswered", "conflict", "expired", "legacy"}:
        return ()
    if (turn.get("scope") or "school") != "school":
        return ()
    return tuple(
        question
        for question in SUGGESTED_FOLLOWUPS.get(turn["domain"], ())
        if normalized(question) != normalized(turn["question"])
    )
