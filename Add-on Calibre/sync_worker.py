# -*- coding: utf-8 -*-
"""
Cœur de la synchronisation — rôle SOURCE uniquement (pas de mode
client dans cette version). Lit Calibre via l'API officielle
db.new_api (jamais SQLite brut pendant que Calibre tourne), calcule
un diff par content_hash, construit les batches, envoie vers
POST /ingest avec idempotency key, et poll les résultats.

Ne dépend que de la bibliothèque standard + de l'API Calibre — aucun
package externe (pas de pip install possible dans l'environnement
Calibre embarqué).
"""

import datetime
import hashlib
import json
import re
import sqlite3
import time
import urllib.request
import urllib.error
import uuid
from html.parser import HTMLParser
from pathlib import Path

from calibre.utils.config import config_dir
from calibre.utils.date import utc_tz

from calibre_plugins.whatepub.config import prefs, SERVER_URL
from calibre_plugins.whatepub.fingerprint import fingerprint_epub


# ---------- État local de synchro (SQLite, séparé de metadata.db) ----------

def get_state_db_path():
    return Path(config_dir) / "plugins" / "whatepub_state.db"


def get_state_conn():
    path = get_state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS addon_sync_state (
            calibre_book_id INTEGER PRIMARY KEY,
            content_hash TEXT,
            last_ingest_id TEXT,
            last_pushed_at TEXT,
            last_known_status TEXT,
            server_book_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingest_retry_queue (
            ingest_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            attempts INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


# ---------- Lecture Calibre (API officielle, jamais SQLite brut) ----------

class _CommentTextExtractor(HTMLParser):
    """Extracteur minimal — mi.comments est du HTML (interne Calibre),
    jamais stocké tel quel : risque XSS stocké si un jour rendu en HTML
    côté admin/API (le contenu vient de n'importe quel membre). Même
    principe que TextExtractor dans fingerprint.py."""
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in ("style", "script") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)

    def get_text(self):
        return "".join(self.parts)


def strip_html_to_text(html_text):
    if not html_text:
        return None
    parser = _CommentTextExtractor()
    try:
        parser.feed(html_text)
    except Exception:
        return None  # au pire, pas de résumé plutôt que du HTML brut stocké
    text = re.sub(r"\s+", " ", parser.get_text()).strip()
    return text or None


def extract_book_fields(db_api, book_id):
    """
    Lit les champs pertinents d'un livre via l'API Calibre officielle.
    Retourne un dict prêt à comparer/sérialiser — jamais d'accès direct
    à metadata.db pendant que Calibre est ouvert (risque de corruption).
    """
    mi = db_api.get_metadata(book_id)

    identifiers = mi.get_identifiers() or {}
    isbn = identifiers.get("isbn")
    viaf_hint = identifiers.get("viaf_author_id") or identifiers.get("viaf")

    authors = list(mi.authors) if mi.authors else []
    series_name = mi.series
    series_index = mi.series_index if mi.series else None

    summary = strip_html_to_text(mi.comments)

    return {
        "calibre_book_id": str(book_id),
        "title": mi.title or "",
        "authors": authors,
        "isbn": isbn,
        "viaf_hint": viaf_hint,
        "language": (mi.languages[0] if mi.languages else None),
        "series_name": series_name,
        "series_index": series_index,
        "summary": summary,
        "has_cover": bool(mi.has_cover),
    }


def compute_content_hash(fields):
    """
    Hash des champs qui comptent pour détecter une modification —
    exclut has_cover (change indépendamment du contenu bibliographique,
    évite un push inutile si juste la couverture est retouchée).
    """
    relevant = {k: v for k, v in fields.items() if k != "has_cover"}
    serialized = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_cover_hash(db_api, book_id):
    """Hash de la couverture telle quelle, sans normalisation (décision actée)."""
    cover_data = db_api.cover(book_id)
    if not cover_data:
        return None
    return hashlib.sha256(cover_data).hexdigest()


def get_epub_path(db_api, book_id, log=print):
    """
    log : injecté pour tracer un échec de format_abspath — avant ce
    correctif, toute exception ici était avalée sans laisser de trace,
    contrairement à l'échec de fingerprint_epub() plus bas qui, lui,
    logue déjà. Un livre dont le CHEMIN pose problème (caractères
    spéciaux, particularité de cette install Calibre) sautait le
    fingerprint sans qu'aucune ligne ne l'indique nulle part.
    """
    try:
        path = db_api.format_abspath(book_id, "EPUB")
        return Path(path) if path else None
    except Exception as e:
        log(f"[whatepub] Échec format_abspath (book_id={book_id}) : {type(e).__name__}: {e}")
        return None


# ---------- Diff : quels livres pousser ----------

def get_books_to_push(db_api, state_conn):
    """
    Compare le contenu actuel de chaque livre Calibre à son état
    connu (content_hash) — retourne uniquement les livres nouveaux ou
    modifiés depuis la dernière synchro.
    """
    to_push = []
    for book_id in db_api.all_book_ids():
        fields = extract_book_fields(db_api, book_id)
        current_hash = compute_content_hash(fields)

        row = state_conn.execute(
            "SELECT content_hash FROM addon_sync_state WHERE calibre_book_id = ?",
            (book_id,),
        ).fetchone()

        if row is None or row["content_hash"] != current_hash:
            to_push.append((book_id, fields, current_hash))

    return to_push


def build_book_payload(db_api, book_id, fields, log=print):
    """
    Construit l'entrée du payload pour un livre. Fingerprint calculé
    SYSTÉMATIQUEMENT (v2, décision actée) — plus seulement pour les
    métadonnées pauvres : un livre bien catalogué doit aussi avoir une
    empreinte pour qu'un livre mal catalogué du même ouvrage puisse le
    retrouver. Coût CPU/IO côté addon uniquement, jamais serveur.
    """
    payload = {
        "calibre_book_id": fields["calibre_book_id"],
        "title": fields["title"],
        "authors": fields["authors"],
        "isbn": fields["isbn"],
        "viaf_hint": fields["viaf_hint"],
        "language": fields["language"],
        "series_name": fields["series_name"],
        "series_index": fields["series_index"],
        "summary": fields["summary"],
        "status": "active",
    }

    if fields["has_cover"]:
        cover_hash = compute_cover_hash(db_api, book_id)
        payload["cover_hash"] = cover_hash
        if cover_hash:
            sync_cover_if_needed(db_api, book_id, cover_hash, log=log)

    epub_path = get_epub_path(db_api, book_id, log=log)
    if epub_path and epub_path.exists():
        try:
            fp = fingerprint_epub(epub_path)
            payload["fingerprint_exact_hash"] = fp["exact_hash"]
            payload["fingerprint_word_count"] = fp["total_words"]
            payload["text_preview"] = fp["text_preview"]
            payload["fingerprint_windows"] = [
                {"pct": w["pct"], "signature": w["signature"], "word_count": w["word_count"]}
                for w in fp["windows"]
            ]
        except Exception as e:
            log(f"[whatepub] Échec fingerprint (book_id={book_id}, path={epub_path}) : {type(e).__name__}: {e}")
            # epub illisible/corrompu — cas normal, ~2.8% mesuré, on
            # continue sans fingerprint plutôt que de bloquer l'envoi
    elif epub_path is not None:
        # format_abspath a renvoyé un chemin mais il n'existe pas sur
        # disque — bibliothèque désynchronisée du système de fichiers.
        # Avant ce correctif, ce cas aussi était totalement silencieux.
        log(f"[whatepub] Chemin epub introuvable sur disque (book_id={book_id}) : {epub_path}")

    return payload


# ---------- Envoi HTTP (urllib, pas de dépendance externe) ----------

def build_multipart_body(field_name, filename, content, content_type):
    """
    multipart/form-data construit à la main — urllib ne le fait pas
    nativement et aucun package externe (requests...) n'est
    installable dans l'environnement Calibre embarqué.
    """
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    body += content
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, boundary


def cover_exists_on_server(server_url, api_key, cover_hash, timeout=15):
    """HEAD /covers/{hash} — évite un envoi inutile si déjà connue côté serveur."""
    req = urllib.request.Request(
        f"{server_url.rstrip('/')}/covers/{cover_hash}",
        headers={"X-API-Key": api_key},
        method="HEAD",
    )
    try:
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def upload_cover_to_server(server_url, api_key, cover_hash, cover_data, timeout=30):
    """POST /covers/{hash} — octets bruts, jamais retraités côté addon."""
    content_type = "image/png" if cover_data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    body, boundary = build_multipart_body("file", "cover", cover_data, content_type)
    req = urllib.request.Request(
        f"{server_url.rstrip('/')}/covers/{cover_hash}",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-API-Key": api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def sync_cover_if_needed(db_api, book_id, cover_hash, log=print):
    """
    Envoie les octets de la cover si le serveur ne les a pas déjà —
    best-effort : un échec ici ne doit jamais faire échouer la synchro
    du reste du livre (les métadonnées partent séparément via /ingest).
    """
    api_key = prefs["api_key"]
    try:
        if cover_exists_on_server(SERVER_URL, api_key, cover_hash):
            return
        cover_data = db_api.cover(book_id)
        if not cover_data:
            return
        upload_cover_to_server(SERVER_URL, api_key, cover_hash, cover_data)
    except Exception as e:
        log(f"[whatepub] Échec envoi cover (book_id={book_id}) : {e}")


def post_ingest(server_url, api_key, ingest_id, books_payload, timeout=30):
    body = json.dumps({"ingest_id": ingest_id, "books": books_payload}).encode("utf-8")
    req = urllib.request.Request(
        f"{server_url.rstrip('/')}/ingest",
        data=body,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get_book_status(server_url, api_key, server_book_id, timeout=15):
    req = urllib.request.Request(
        f"{server_url.rstrip('/')}/books/{server_book_id}",
        headers={"X-API-Key": api_key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def lookup_by_fingerprint(server_url, api_key, exact_hash, word_count, windows, timeout=15):
    """POST /lookup — envoie UNIQUEMENT la signature (jamais titre/auteur/
    isbn), reçoit en retour les infos de l'œuvre correspondante si le
    serveur en trouve une. Lecture seule côté serveur (rien n'est créé
    ni modifié en base) ; ne modifie jamais les métadonnées Calibre non
    plus, ça reste une décision séparée, pas prise ici."""
    body = json.dumps({
        "exact_hash": exact_hash,
        "word_count": word_count,
        "windows": windows,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{server_url.rstrip('/')}/lookup",
        data=body,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def lookup_book_by_signature(epub_path, api_key, timeout=15):
    """Calcule la signature de epub_path et interroge lookup_by_fingerprint.
    Ne touche jamais au fichier ni à ses métadonnées — fingerprint_epub
    est documenté LECTURE SEULE (voir fingerprint.py)."""
    fp = fingerprint_epub(epub_path)
    windows = [
        {"pct": w["pct"], "signature": w["signature"], "word_count": w["word_count"]}
        for w in fp["windows"]
    ]
    return lookup_by_fingerprint(SERVER_URL, api_key, fp["exact_hash"], fp["total_words"], windows, timeout=timeout)


def fetch_cover_bytes(cover_hash, timeout=15):
    """GET /covers/{hash} — public, sans authentification (même route
    que celle utilisée côté admin en <img src="...">, cf. server/main.py)."""
    req = urllib.request.Request(f"{SERVER_URL.rstrip('/')}/covers/{cover_hash}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fix_author_casing(db_api, book_id, wanted_name, log=print):
    """Calibre lie les auteurs à un livre PAR NOM en ignorant la casse
    (table interne authors, unicité insensible à la casse) — assigner
    mi.authors = [wanted_name] puis set_metadata() ne suffit donc pas à
    corriger la casse d'un auteur déjà présent dans la bibliothèque sous
    une autre casse : Calibre retrouve l'entrée existante par comparaison
    insensible à la casse et garde SA casse d'origine (ex. book resté sur
    "Pierre DAC" après avoir demandé "Pierre Dac" — cas rencontré en
    prod). Seul un renommage explicite de l'entrée authors (rename_author,
    par id) corrige réellement l'affichage. Best-effort : le reste des
    métadonnées est déjà appliqué, un échec ici ne doit pas le remettre
    en cause."""
    try:
        author_ids = db_api.fields["authors"].ids_for_book(book_id)
        for author_id in author_ids:
            current = db_api.author_data([author_id]).get(author_id, {}).get("name")
            if current and current != wanted_name and current.lower() == wanted_name.lower():
                db_api.rename_author(author_id, wanted_name)
    except Exception as e:
        log(f"[whatepub] Échec correction casse auteur (book_id={book_id}) : {type(e).__name__}: {e}")


def apply_work_metadata(db_api, book_id, work, log=print):
    """Écrase les métadonnées Calibre locales du livre sélectionné avec
    celles de l'œuvre résolue côté serveur (work, tel que renvoyé par
    /lookup). DESTRUCTIF pour les métadonnées locales — jamais appelé
    automatiquement (voir lookup_by_fingerprint) : uniquement à la
    demande explicite de l'utilisateur, via le bouton "Mettre à jour mes
    métadonnées" de la fenêtre de vérification. N'écrase un champ que si
    le serveur a une valeur pour lui, jamais avec du vide."""
    mi = db_api.get_metadata(book_id)

    if work.get("title"):
        mi.title = work["title"]
    if work.get("author"):
        mi.authors = [work["author"]]
    if work.get("series_name"):
        mi.series = work["series_name"]
        if work.get("series_index") is not None:
            mi.series_index = float(work["series_index"])
    if work.get("language"):
        mi.languages = [work["language"]]
    if work.get("publication_year"):
        # Le serveur ne connaît que l'année (pas de mois/jour) — 1er
        # janvier en approximation, comme les autres plugins de
        # métadonnées Calibre quand seule l'année est connue.
        mi.pubdate = datetime.datetime(int(work["publication_year"]), 1, 1, tzinfo=utc_tz)
    if work.get("summary"):
        mi.comments = work["summary"]
    if work.get("external_ids"):
        identifiers = mi.get_identifiers() or {}
        identifiers.update(work["external_ids"])
        mi.set_identifiers(identifiers)

    db_api.set_metadata(book_id, mi)

    if work.get("author"):
        _fix_author_casing(db_api, book_id, work["author"], log=log)

    if work.get("cover_hash"):
        try:
            cover_data = fetch_cover_bytes(work["cover_hash"])
            db_api.set_cover(book_id, cover_data)
        except Exception as e:
            # Best-effort, comme sync_cover_if_needed : le reste des
            # métadonnées est déjà appliqué, pas de raison de le perdre
            # pour un échec de récupération de la seule couverture.
            log(f"[whatepub] Échec récupération cover (book_id={book_id}) : {type(e).__name__}: {e}")


def push_single_book(db_api, book_id, api_key, log=print):
    """Envoie UN livre vers POST /ingest, à la demande (menu "Envoyer le
    livre sélectionné") — même logique que run_scan_and_push mais pour
    un seul livre, hors cycle automatique. Crée la fiche work côté
    serveur si elle n'existe pas encore (résolution asynchrone ensuite,
    comme pour un push normal). Met à jour addon_sync_state pour que ce
    livre ne soit pas repoussé en double au prochain scan automatique.
    Retourne le server_book_id (utile pour un futur suivi de statut)."""
    fields = extract_book_fields(db_api, book_id)
    content_hash = compute_content_hash(fields)
    payload = build_book_payload(db_api, book_id, fields, log=log)
    ingest_id = str(uuid.uuid4())

    result = post_ingest(SERVER_URL, api_key, ingest_id, [payload])
    server_book_ids = result.get("book_ids", [])
    server_book_id = server_book_ids[0] if server_book_ids else None

    state_conn = get_state_conn()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    state_conn.execute(
        """
        INSERT INTO addon_sync_state
            (calibre_book_id, content_hash, last_ingest_id, last_pushed_at,
             last_known_status, server_book_id)
        VALUES (?, ?, ?, ?, 'accepted', ?)
        ON CONFLICT(calibre_book_id) DO UPDATE SET
            content_hash = excluded.content_hash,
            last_ingest_id = excluded.last_ingest_id,
            last_pushed_at = excluded.last_pushed_at,
            last_known_status = 'accepted',
            server_book_id = excluded.server_book_id
        """,
        (book_id, content_hash, ingest_id, now, server_book_id),
    )
    state_conn.commit()
    state_conn.close()

    return server_book_id


# ---------- Cycle complet : scan + push ----------

def run_scan_and_push(db_api, log=print):
    """
    Un cycle complet : diff -> batches -> envoi idempotent. Retourne
    un résumé (nb poussés, nb échecs) pour affichage dans l'UI.
    """
    api_key = prefs["api_key"]
    batch_size = prefs["batch_size"]

    if not api_key:
        log("[whatepub] Aucune clé API configurée — synchro annulée.")
        return {"pushed": 0, "failed": 0, "skipped": True}

    state_conn = get_state_conn()

    to_push = get_books_to_push(db_api, state_conn)
    if not to_push:
        log("[whatepub] Rien à synchroniser.")
        return {"pushed": 0, "failed": 0, "skipped": False}

    log(f"[whatepub] {len(to_push)} livre(s) à pousser.")

    pushed, failed = 0, 0

    for i in range(0, len(to_push), batch_size):
        batch = to_push[i:i + batch_size]
        books_payload = [
            build_book_payload(db_api, book_id, fields, log=log)
            for book_id, fields, _ in batch
        ]
        ingest_id = str(uuid.uuid4())

        try:
            result = post_ingest(SERVER_URL, api_key, ingest_id, books_payload)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            log(f"[whatepub] Échec envoi batch ({len(batch)} livres) : {e} — retry au prochain cycle.")
            state_conn.execute(
                "INSERT OR REPLACE INTO ingest_retry_queue (ingest_id, payload_json) VALUES (?, ?)",
                (ingest_id, json.dumps(books_payload)),
            )
            state_conn.commit()
            failed += len(batch)
            continue

        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        for (book_id, fields, content_hash), server_book_id in zip(batch, result.get("book_ids", [])):
            state_conn.execute(
                """
                INSERT INTO addon_sync_state
                    (calibre_book_id, content_hash, last_ingest_id, last_pushed_at,
                     last_known_status, server_book_id)
                VALUES (?, ?, ?, ?, 'accepted', ?)
                ON CONFLICT(calibre_book_id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    last_ingest_id = excluded.last_ingest_id,
                    last_pushed_at = excluded.last_pushed_at,
                    last_known_status = 'accepted',
                    server_book_id = excluded.server_book_id
                """,
                (book_id, content_hash, ingest_id, now, server_book_id),
            )
            pushed += 1
        state_conn.commit()

    state_conn.close()
    log(f"[whatepub] Synchro terminée : {pushed} poussé(s), {failed} échec(s).")
    return {"pushed": pushed, "failed": failed, "skipped": False}


# ---------- Cycle complet : poll des résultats ----------

def run_poll_results(log=print):
    """
    Interroge GET /books/{id} pour les livres encore 'accepted' —
    met à jour le statut local, journalise les suggestions en attente
    (l'affichage/confirmation UI est un chantier séparé, pas encore
    construit dans cette version : rôle CLIENT de l'addon).
    """
    api_key = prefs["api_key"]

    if not api_key:
        return {"checked": 0, "resolved": 0}

    state_conn = get_state_conn()
    pending_rows = state_conn.execute(
        "SELECT calibre_book_id, server_book_id FROM addon_sync_state WHERE last_known_status = 'accepted'"
    ).fetchall()

    if not pending_rows:
        state_conn.close()
        return {"checked": 0, "resolved": 0}

    resolved = 0
    for row in pending_rows:
        try:
            book = get_book_status(SERVER_URL, api_key, row["server_book_id"])
        except Exception as e:
            log(f"[whatepub] Échec poll livre {row['calibre_book_id']} : {e}")
            continue

        if book.get("work_id") is not None:
            state_conn.execute(
                "UPDATE addon_sync_state SET last_known_status = 'resolved' WHERE calibre_book_id = ?",
                (row["calibre_book_id"],),
            )
            resolved += 1
        elif book.get("pending_suggestion"):
            state_conn.execute(
                "UPDATE addon_sync_state SET last_known_status = 'awaiting_confirmation' WHERE calibre_book_id = ?",
                (row["calibre_book_id"],),
            )
            log(f"[whatepub] Suggestion en attente pour le livre {row['calibre_book_id']} "
                f"(confirmation manuelle nécessaire — UI à venir)")

    state_conn.commit()
    state_conn.close()

    if resolved:
        log(f"[whatepub] {resolved} livre(s) résolu(s) depuis le dernier poll.")

    return {"checked": len(pending_rows), "resolved": resolved}
