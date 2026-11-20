"""Clients Radarr (films) et Sonarr (séries) : ajout d'un titre validé par l'administrateur, puis recherche.

API v3, commune à Radarr 3 à 5 et Sonarr 3 et 4. Adresse et clé réglées par l'administrateur (réseau local) :
seule une demande acceptée (par lui, ou par un membre qu'il autorise à demander directement) arrive ici.
"""

import httpx

from app.services import arr_categories
from app.services.errors import ServiceError
from app.services.http import http

LABELS = {"radarr": "Radarr", "sonarr": "Sonarr"}


class ArrClient:
    def __init__(self, kind: str, url: str, api_key: str, categories: list[dict] | None = None):
        self.kind = kind
        self.label = LABELS[kind]
        self.url = url.rstrip("/")
        self.api_key = api_key.strip()
        self.categories = categories or []

    @property
    def ready(self) -> bool:
        """Prêt à recevoir des demandes : au moins une catégorie avec son dossier et sa qualité."""
        return bool(arr_categories.complete(self.categories))

    async def _req(self, method: str, path: str, params: dict | None = None, json: dict | None = None):
        try:
            r = await http().request(
                method, f"{self.url}/api/v3{path}", params=params, json=json,
                headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            raise ServiceError(self.kind, f"{self.label} injoignable : {e.__class__.__name__}") from e
        if r.status_code in (401, 403):
            raise ServiceError(self.kind, f"Clé API {self.label} invalide", 401)
        if r.status_code >= 400:
            raise ServiceError(self.kind, f"Erreur {self.label} ({r.status_code}){_reason(r)}")
        try:
            return r.json() if r.content else None
        except ValueError as e:  # page HTML : mauvaise adresse (ou base d'URL manquante)
            raise ServiceError(self.kind, f"Réponse inattendue : est-ce bien l'adresse de {self.label} ?") from e

    async def test(self) -> dict:
        status = await self._req("GET", "/system/status") or {}
        if status.get("appName", self.label).lower() != self.kind:
            raise ServiceError(self.kind, f"Cette adresse est celle de {status.get('appName')}, pas de {self.label}")
        return {"version": status.get("version"), **await self.options()}

    async def options(self) -> dict:
        """Dossiers de destination et profils de qualité, au choix de l'administrateur."""
        folders = await self._req("GET", "/rootfolder") or []
        profiles = await self._req("GET", "/qualityprofile") or []
        return {
            "root_folders": [{"path": f["path"], "free": f.get("freeSpace")} for f in folders if f.get("path")],
            "quality_profiles": [{"id": p["id"], "name": p.get("name", "")} for p in profiles if p.get("id")],
        }

    async def _command(self, name: str, **body) -> None:
        await self._req("POST", "/command", json={"name": name, **body})

    # --- Radarr ---------------------------------------------------------------------------------

    async def add_movie(self, tmdb_id: int, root_folder: str, quality_profile: int) -> str:
        """Ajoute le film (surveillé, recherche lancée) ; déjà présent : relance la recherche.
        Renvoie "available" (fichier déjà là), "searching" ou "added"."""
        existing = await self._req("GET", "/movie", params={"tmdbId": tmdb_id}) or []
        if existing:
            movie = existing[0]
            if movie.get("hasFile"):
                return "available"
            if not movie.get("monitored"):
                await self._req("PUT", f"/movie/{movie['id']}", json={**movie, "monitored": True})
            await self._command("MoviesSearch", movieIds=[movie["id"]])
            return "searching"
        found = await self._req("GET", "/movie/lookup/tmdb", params={"tmdbId": tmdb_id})
        if not found:
            raise ServiceError(self.kind, "Film introuvable dans Radarr", 404)
        await self._req("POST", "/movie", json={
            **found, "qualityProfileId": quality_profile, "rootFolderPath": root_folder, "monitored": True,
            # Pas avant la sortie (numérique ou physique) : une demande faite pendant le cinéma attend son tour
            "minimumAvailability": "released", "addOptions": {"searchForMovie": True},
        })
        return "added"

    # --- Sonarr ---------------------------------------------------------------------------------

    async def add_series(self, tvdb_id: int, root_folder: str, quality_profile: int, seasons: list[int] | None = None,
                         series_type: str = "standard") -> str:
        """Ajoute la série avec les saisons demandées (toutes si None, hors épisodes spéciaux), recherche lancée.
        series_type « anime » : numérotation absolue des animes. Déjà présente : surveille aussi ces saisons et relance
        la recherche."""
        def wanted(n: int) -> bool:
            return n > 0 and (seasons is None or n in seasons)

        existing = await self._req("GET", "/series", params={"tvdbId": tvdb_id}) or []
        if existing:
            series = existing[0]
            series["monitored"] = True
            for s in series.get("seasons", []):
                s["monitored"] = s.get("monitored", False) or wanted(s["seasonNumber"])
            await self._req("PUT", f"/series/{series['id']}", json=series)
            await self._command("SeriesSearch", seriesId=series["id"])
            return "searching"
        found = await self._req("GET", "/series/lookup", params={"term": f"tvdb:{tvdb_id}"}) or []
        if not found:
            raise ServiceError(self.kind, "Série introuvable dans Sonarr", 404)
        series = found[0]
        body = {
            **series, "qualityProfileId": quality_profile, "rootFolderPath": root_folder, "monitored": True,
            "seriesType": series_type, "seasonFolder": True, "seasons": [{**s, "monitored": wanted(s["seasonNumber"])} for s in series.get("seasons", [])],
            "addOptions": {"searchForMissingEpisodes": True, "ignoreEpisodesWithFiles": True},
        }
        status = await self._req("GET", "/system/status") or {}
        if str(status.get("version", "")).startswith("3."):  # Sonarr 3 exige aussi un profil de langue
            langs = await self._req("GET", "/languageprofile") or []
            if langs:
                body["languageProfileId"] = langs[0]["id"]
        await self._req("POST", "/series", json=body)
        return "added"


def _reason(r: httpx.Response) -> str:
    """Motif renvoyé par Radarr / Sonarr (liste d'erreurs de validation ou message), court."""
    try:
        data = r.json()
    except ValueError:
        return ""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        msg = data[0].get("errorMessage")
    elif isinstance(data, dict):
        msg = data.get("message")
    else:
        msg = None
    return f" : {str(msg)[:200]}" if msg else ""
