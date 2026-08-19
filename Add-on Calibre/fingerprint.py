#!/usr/bin/env python3
"""
Fingerprinting d'un epub — LECTURE SEULE.

Pipeline v2 (validé par tests réels sur plusieurs éditions de Germinal,
Zola — voir doc de reprise) :

    epub → texte complet (tout le spine, dans l'ordre de lecture)
         → normalisation (minuscules, accents supprimés, ponctuation/
                            espaces uniformisés)
         → SHA-256(texte normalisé complet)          = exact_hash
         → 6 fenêtres réparties entre 10% et 90% de la longueur totale
              → shingles (5-grams de mots)
              → MinHash (128 permutations) par fenêtre

Remplace la v1 (2 fenêtres à position FIXE en mots — début + offset
3000 mots) : testé en conditions réelles, la v1 s'effondre dès qu'une
préface de quelques centaines de mots décale le texte entre éditions.
Les fenêtres par pourcentage restent robustes sur les cas réels
observés (4 éditions de Germinal, aucune n'a le même exact_hash mais
toutes > 0.66 de similarité par fenêtre). Note honnête : ça n'élimine
pas le problème sur une préface franchement longue (testé jusqu'à
3000 mots, effondrement partiel constaté) — jugé acceptable, le seul
coût d'un match manqué est un livre qui reste non résolu, jamais une
fusion incorrecte.

Aucune dépendance externe — uniquement la bibliothèque standard Python.
"""

import re
import hashlib
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from html.parser import HTMLParser


# ---------- Extraction HTML → texte (exclut style/script) ----------

class TextExtractor(HTMLParser):
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
        return " ".join(self.parts)


def html_to_text(html: str) -> str:
    parser = TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = parser.get_text()
    return re.sub(r"\s+", " ", text).strip()


# ---------- Extraction epub complète (tout le spine) ----------

def extract_epub_full_text(epub_path: Path) -> str:
    """
    Suit container.xml -> content.opf -> spine, lit TOUS les fichiers
    HTML/XHTML dans l'ordre de lecture, sans limite de mots.
    """
    with zipfile.ZipFile(epub_path, "r") as zf:
        container_xml = zf.read("META-INF/container.xml")
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
        root = ET.fromstring(container_xml)
        rootfile = root.find(".//c:rootfile", ns)
        opf_path = rootfile.attrib["full-path"]
        opf_dir = str(Path(opf_path).parent)

        opf_content = zf.read(opf_path)
        opf_ns = {"opf": "http://www.idpf.org/2007/opf"}
        opf_root = ET.fromstring(opf_content)

        manifest = {
            item.attrib["id"]: item.attrib["href"]
            for item in opf_root.findall(".//opf:manifest/opf:item", opf_ns)
        }
        spine_ids = [
            itemref.attrib["idref"]
            for itemref in opf_root.findall(".//opf:spine/opf:itemref", opf_ns)
        ]

        text_chunks = []
        for item_id in spine_ids:
            href = manifest.get(item_id)
            if not href:
                continue
            full_path = f"{opf_dir}/{href}" if opf_dir != "." else href
            try:
                raw = zf.read(full_path).decode("utf-8", errors="ignore")
            except KeyError:
                continue
            text_chunks.append(html_to_text(raw))

        return " ".join(text_chunks)


# ---------- Normalisation ----------

def normalize_text(text: str) -> str:
    """
    Minuscules, suppression des accents, ponctuation retirée,
    espaces uniformisés.
    """
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------- Hash exact ----------

def compute_exact_hash(normalized_text: str) -> str:
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


# ---------- MinHash (implémentation autonome, sans dépendance) ----------

NUM_PERM = 128
_SEEDS = [i for i in range(NUM_PERM)]


def get_shingles(text: str, k: int = 5) -> set:
    """Découpe en séquences de k mots qui se chevauchent (5-grams)."""
    words = text.split()
    if len(words) < k:
        return {text} if text else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _hash_with_seed(shingle: str, seed: int) -> int:
    h = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8, person=str(seed).encode())
    return int.from_bytes(h.digest(), "big")


def compute_minhash(shingles: set) -> list:
    if not shingles:
        return [0] * NUM_PERM
    signature = []
    for seed in _SEEDS:
        min_val = min(_hash_with_seed(s, seed) for s in shingles)
        signature.append(min_val)
    return signature


def minhash_similarity(sig_a: list, sig_b: list) -> float:
    """Estimation de similarité de Jaccard : proportion de valeurs identiques."""
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    matches = sum(1 for a, b in zip(sig_a, sig_b) if a == b)
    return matches / len(sig_a)


# ---------- Fenêtres réparties par pourcentage (v2) ----------

NUM_WINDOWS = 6
WINDOW_WORD_LEN = 1000
PCT_MIN = 0.10
PCT_MAX = 0.90


def get_windows_by_percentage(normalized_text: str, num_windows: int = NUM_WINDOWS,
                               window_word_len: int = WINDOW_WORD_LEN):
    """
    num_windows fenêtres réparties uniformément entre 10% et 90% de la
    longueur du texte — évite les tout premiers/derniers mots (page de
    titre, mentions légales, table des matières), reste dans le corps
    du texte réel. Testé en conditions réelles sur 4 éditions de
    Germinal : robuste tant que les préfaces entre éditions restent de
    longueur comparable (voir docstring du module pour la limite connue).
    """
    words = normalized_text.split()
    total = len(words)
    if total == 0:
        return []

    windows = []
    for i in range(num_windows):
        pct = PCT_MIN + ((PCT_MAX - PCT_MIN) * i / max(num_windows - 1, 1))
        center = int(total * pct)
        start = max(0, center - window_word_len // 2)
        end = min(total, start + window_word_len)
        window_text = " ".join(words[start:end])
        windows.append({"pct": round(pct * 100, 1), "text": window_text})
    return windows


# ---------- Fingerprint complet ----------

def get_text_preview(full_text: str, num_words: int = 200) -> str:
    """
    Aperçu LISIBLE (pas la version normalisée du fingerprint —
    minuscules/accents retirés, illisible). Juste le texte extrait,
    nettoyé du HTML, tronqué aux premiers mots. Usage interne (admin) :
    vérification manuelle d'un titre/langue douteux sur un livre non
    résolu, jamais exposé publiquement.
    """
    words = full_text.split()
    return " ".join(words[:num_words])


def fingerprint_epub(epub_path: Path) -> dict:
    full_text = extract_epub_full_text(epub_path)
    normalized = normalize_text(full_text)

    exact_hash = compute_exact_hash(normalized)
    total_words = len(normalized.split())
    text_preview = get_text_preview(full_text)

    windows_raw = get_windows_by_percentage(normalized)
    windows = []
    for w in windows_raw:
        shingles = get_shingles(w["text"])
        signature = compute_minhash(shingles)
        windows.append({
            "pct": w["pct"],
            "signature": signature,
            "word_count": len(w["text"].split()),
        })

    return {
        "epub_path": str(epub_path),
        "total_words": total_words,
        "exact_hash": exact_hash,
        "text_preview": text_preview,
        "windows": windows,
    }


if __name__ == "__main__":
    import sys
    import json

    result = fingerprint_epub(Path(sys.argv[1]))
    print(f"{result['epub_path']}: {result['total_words']} mots")
    print(f"exact_hash: {result['exact_hash']}")
    for w in result["windows"]:
        print(f"  fenêtre {w['pct']}% : {w['word_count']} mots, signature[:3]={w['signature'][:3]}")
