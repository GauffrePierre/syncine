"""Catégories Radarr / Sonarr : chacune a son dossier de destination et sa qualité par défaut.

L'administrateur les crée (films, spectacles, reportages, concerts ; séries, dessins animés, animes…) et indique pour
quel genre chacune est proposée. En acceptant une demande, SynCiné propose la catégorie qui correspond au titre (genres
et mots-clés TMDB) ; l'administrateur peut en choisir une autre, et une autre qualité.
"""

import json

# Genre auquel une catégorie s'applique (« default » : tout le reste)
RULES = ("default", "documentary", "animation", "anime", "music", "standup")
# Ordre de préférence quand un titre correspond à plusieurs : un anime est aussi de l'animation
PRIORITY = ("anime", "music", "standup", "documentary", "animation")
DEFAULT_NAMES = {"radarr": "Films", "sonarr": "Séries"}
STANDUP_KEYWORD = 9716  # TMDB : « stand-up comedy »


def load(s, kind: str) -> list[dict]:
    """Catégories réglées. Ancien réglage (un seul dossier et une qualité) : une catégorie « Films » ou « Séries »."""
    try:
        cats = json.loads(getattr(s, f"{kind}_categories") or "[]")
    except ValueError:
        cats = []
    cats = [c for c in cats if isinstance(c, dict) and c.get("id")]
    if not cats and getattr(s, f"{kind}_root_folder", "") and getattr(s, f"{kind}_quality_profile", 0):
        cats = [{"id": "default", "name": DEFAULT_NAMES[kind], "rule": "default",
                 "root_folder": getattr(s, f"{kind}_root_folder"), "quality_profile": getattr(s, f"{kind}_quality_profile")}]
    return cats


def complete(cats: list[dict]) -> list[dict]:
    """Catégories utilisables : dossier et qualité choisis."""
    return [c for c in cats if c.get("root_folder") and c.get("quality_profile")]


def rules_of(raw: dict) -> set[str]:
    """Genres reconnus d'un titre, d'après sa fiche TMDB complète (detail_raw)."""
    genres = {g.get("id") for g in raw.get("genres", [])}
    kw = raw.get("keywords") or {}
    keywords = {k.get("id") for k in kw.get("keywords", kw.get("results", []))}
    found = set()
    if 99 in genres:
        found.add("documentary")
    if 16 in genres:
        found.add("animation")
        if raw.get("original_language") == "ja" or "JP" in (raw.get("origin_country") or []):
            found.add("anime")
    if 10402 in genres:
        found.add("music")
    if STANDUP_KEYWORD in keywords:
        found.add("standup")
    return found


def suggest(cats: list[dict], raw: dict) -> dict | None:
    """Catégorie proposée pour ce titre : celle de son genre le plus précis, sinon celle par défaut, sinon la première."""
    cats = complete(cats)
    if not cats:
        return None
    found = rules_of(raw)
    for rule in PRIORITY:
        if rule in found and (match := next((c for c in cats if c.get("rule") == rule), None)):
            return match
    return next((c for c in cats if c.get("rule") == "default"), cats[0])


def find(cats: list[dict], cat_id: str | None) -> dict | None:
    return next((c for c in complete(cats) if c["id"] == cat_id), None) if cat_id else None
