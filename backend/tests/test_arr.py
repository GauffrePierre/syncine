"""Radarr / Sonarr : ajout des titres demandés une fois acceptés, réglages réservés à l'administrateur principal."""

import asyncio

import pytest

from app.database import SessionLocal
from app.models import UserSettings
from app.services import tmdb_client
from app.services.arr_client import ArrClient
from app.services.errors import ServiceError
from tests.test_accounts import add_member, client, fresh, link_plex, login, setup_admin  # noqa: F401 (fixture)


class FakeArr:
    """Remplace les appels HTTP : réponses par (méthode, chemin), appels enregistrés."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []

    async def __call__(self, method, path, params=None, json=None):
        self.calls.append((method, path, params, json))
        return self.responses.get((method, path))

    def sent(self, method, path) -> list[dict]:
        return [c[3] for c in self.calls if c[0] == method and c[1] == path]


def _client(monkeypatch, kind, responses) -> tuple[ArrClient, FakeArr]:
    c = ArrClient(kind, "http://arr.test:7878/", "cle")
    fake = FakeArr(responses)
    monkeypatch.setattr(c, "_req", fake)
    return c, fake


# --- Client ----------------------------------------------------------------------------------


def test_new_movie_is_added_monitored_with_a_search(monkeypatch):
    c, fake = _client(monkeypatch, "radarr", {("GET", "/movie"): [], ("GET", "/movie/lookup/tmdb"): {"title": "Matrix", "tmdbId": 603}})
    assert asyncio.run(c.add_movie(603, "/data/films", 4)) == "added"
    [body] = fake.sent("POST", "/movie")
    assert body["tmdbId"] == 603 and body["qualityProfileId"] == 4 and body["rootFolderPath"] == "/data/films"
    assert body["monitored"] and body["minimumAvailability"] == "released" and body["addOptions"] == {"searchForMovie": True}


def test_movie_already_downloaded_is_available_and_not_added_again(monkeypatch):
    c, fake = _client(monkeypatch, "radarr", {("GET", "/movie"): [{"id": 9, "hasFile": True, "monitored": True}]})
    assert asyncio.run(c.add_movie(603, "/data/films", 4)) == "available"
    assert not fake.sent("POST", "/movie") and not fake.sent("POST", "/command")


def test_movie_already_listed_is_monitored_and_searched_again(monkeypatch):
    c, fake = _client(monkeypatch, "radarr", {("GET", "/movie"): [{"id": 9, "hasFile": False, "monitored": False}]})
    assert asyncio.run(c.add_movie(603, "/data/films", 4)) == "searching"
    assert fake.sent("PUT", "/movie/9")[0]["monitored"] is True
    assert fake.sent("POST", "/command") == [{"name": "MoviesSearch", "movieIds": [9]}]


def test_new_series_monitors_only_the_requested_seasons(monkeypatch):
    seasons = [{"seasonNumber": n, "monitored": True} for n in (0, 1, 2)]
    c, fake = _client(monkeypatch, "sonarr", {
        ("GET", "/series"): [], ("GET", "/series/lookup"): [{"title": "Dark", "tvdbId": 334824, "seasons": seasons}],
        ("GET", "/system/status"): {"version": "4.0.10"},
    })
    assert asyncio.run(c.add_series(334824, "/data/series", 6, [2], "anime")) == "added"
    assert fake.calls[1][2] == {"term": "tvdb:334824"}
    [body] = fake.sent("POST", "/series")
    assert {s["seasonNumber"]: s["monitored"] for s in body["seasons"]} == {0: False, 1: False, 2: True}
    assert body["addOptions"]["searchForMissingEpisodes"] and "languageProfileId" not in body
    assert body["seriesType"] == "anime" and body["rootFolderPath"] == "/data/series" and body["qualityProfileId"] == 6


def test_all_seasons_without_specials_and_sonarr_3_language_profile(monkeypatch):
    seasons = [{"seasonNumber": n} for n in (0, 1, 2)]
    c, fake = _client(monkeypatch, "sonarr", {
        ("GET", "/series"): [], ("GET", "/series/lookup"): [{"tvdbId": 1, "seasons": seasons}],
        ("GET", "/system/status"): {"version": "3.0.10"}, ("GET", "/languageprofile"): [{"id": 7}],
    })
    asyncio.run(c.add_series(1, "/data/series", 6))
    [body] = fake.sent("POST", "/series")
    assert [s["monitored"] for s in body["seasons"]] == [False, True, True] and body["languageProfileId"] == 7


def test_series_already_listed_keeps_its_seasons_and_adds_the_requested_ones(monkeypatch):
    series = {"id": 5, "monitored": False, "seasons": [{"seasonNumber": 1, "monitored": True}, {"seasonNumber": 2, "monitored": False},
                                                      {"seasonNumber": 3, "monitored": False}]}
    c, fake = _client(monkeypatch, "sonarr", {("GET", "/series"): [series]})
    assert asyncio.run(c.add_series(1, "/data/series", 6, [3])) == "searching"
    [body] = fake.sent("PUT", "/series/5")
    assert body["monitored"] and [s["monitored"] for s in body["seasons"]] == [True, False, True]
    assert fake.sent("POST", "/command") == [{"name": "SeriesSearch", "seriesId": 5}]


def test_test_refuses_the_wrong_application(monkeypatch):
    c, _ = _client(monkeypatch, "radarr", {("GET", "/system/status"): {"appName": "Sonarr", "version": "4"}})
    with pytest.raises(ServiceError, match="pas de Radarr"):
        asyncio.run(c.test())


# --- Demandes --------------------------------------------------------------------------------


FILMS = {"id": "films", "name": "Films", "rule": "default", "root_folder": "/data/films", "quality_profile": 4}
DOCS = {"id": "docs", "name": "Reportages", "rule": "documentary", "root_folder": "/data/docs", "quality_profile": 4}


def _configure(**fields):
    import json

    base = {"radarr_url": "http://arr.test:7878", "radarr_api_key": "cle-radarr", "radarr_categories": json.dumps([FILMS])}
    fields = {k: json.dumps(v) if k.endswith("_categories") else v for k, v in fields.items()}
    with SessionLocal() as db:
        s = db.get(UserSettings, 1)
        for k, v in {**base, **fields}.items():
            setattr(s, k, v)
        db.commit()


def _member_with_request(admin, media_type="movie", tmdb_id=603):
    member = add_member(admin)
    link_plex(member["id"])
    alice = login("alice", "provisoire1")
    alice.post("/api/auth/password", json={"new_password": "nouveau-mdp-alice"})
    r = alice.post("/api/requests", json={"tmdb_id": tmdb_id, "media_type": media_type, "title": "Titre"}).json()
    assert r["request"]["status"] == "pending"
    return alice, r["request"]["id"]


def test_accepted_movie_request_goes_to_radarr_and_the_member_is_told(fresh, monkeypatch):  # noqa: F811
    admin = client()
    setup_admin(admin)
    _configure()
    added = []

    async def add_movie(self, tmdb_id, root_folder, quality_profile):
        added.append((root_folder, quality_profile, tmdb_id))
        return "added"

    monkeypatch.setattr(ArrClient, "add_movie", add_movie)
    alice, rid = _member_with_request(admin)
    assert added == []  # rien n'est téléchargé avant l'accord de l'administrateur
    queue = admin.get("/api/requests?scope=all").json()
    assert queue["send_to"] == {"movie": "radarr", "tv": None}
    r = admin.post(f"/api/requests/{rid}/send")
    assert r.status_code == 200 and r.json()["status"] == "sent" and r.json()["sent_to"] == "radarr"
    assert added == [("/data/films", 4, 603)]
    assert any("acceptée" in n["message"] for n in alice.get("/api/notifications").json()["results"])
    assert admin.post(f"/api/requests/{rid}/send").status_code == 409  # pas deux fois
    assert alice.post(f"/api/requests/{rid}/send").status_code == 403


def test_accepted_series_goes_to_sonarr_with_its_tvdb_id(fresh, monkeypatch):  # noqa: F811
    admin = client()
    setup_admin(admin)
    series = {"id": "series", "name": "Séries", "rule": "default", "root_folder": "/data/series", "quality_profile": 6}
    animes = {"id": "animes", "name": "Animes", "rule": "anime", "root_folder": "/data/animes", "quality_profile": 7}
    _configure(sonarr_url="http://arr.test:8989", sonarr_api_key="cle-sonarr", sonarr_categories=[series, animes])
    added = []

    async def add_series(self, tvdb_id, root_folder, quality_profile, seasons=None, series_type="standard"):
        added.append((self.kind, tvdb_id, root_folder, quality_profile, series_type))
        return "added"

    async def detail_raw(self, media_type, tmdb_id):
        return {"external_ids": {"tvdb_id": 334824}, "genres": [{"id": 16}], "original_language": "ja"}

    monkeypatch.setattr(ArrClient, "add_series", add_series)
    monkeypatch.setattr(tmdb_client.TMDBClient, "detail_raw", detail_raw)
    with SessionLocal() as db:
        db.get(UserSettings, 1).tmdb_api_key = "cle-tmdb"
        db.commit()
    _, rid = _member_with_request(admin, "tv", 70523)
    r = admin.post(f"/api/requests/{rid}/send").json()
    assert r["sent_to"] == "sonarr" and r["sent_category"] == "Animes"
    assert added == [("sonarr", 334824, "/data/animes", 7, "anime")]  # animation japonaise : catégorie Animes proposée


def test_radarr_already_has_the_file_marks_the_request_available(fresh, monkeypatch):  # noqa: F811
    admin = client()
    setup_admin(admin)
    _configure()

    async def add_movie(self, tmdb_id, root_folder, quality_profile):
        return "available"

    monkeypatch.setattr(ArrClient, "add_movie", add_movie)
    _, rid = _member_with_request(admin)
    assert admin.post(f"/api/requests/{rid}/send").json()["status"] == "available"


def test_radarr_without_folder_or_quality_is_not_used(fresh):  # noqa: F811
    admin = client()
    setup_admin(admin)
    _configure(radarr_categories=[{**FILMS, "quality_profile": 0}])
    _, rid = _member_with_request(admin)
    assert admin.get("/api/requests?scope=all").json()["send_to"]["movie"] is None
    r = admin.post(f"/api/requests/{rid}/send")
    assert r.status_code == 409 and "Ni Radarr ni Seerr" in r.json()["detail"]


def test_admin_own_request_is_sent_straight_away(fresh, monkeypatch):  # noqa: F811
    admin = client()
    setup_admin(admin)
    _configure()

    async def add_movie(self, tmdb_id, root_folder, quality_profile):
        return "added"

    monkeypatch.setattr(ArrClient, "add_movie", add_movie)
    r = admin.post("/api/requests", json={"tmdb_id": 603, "media_type": "movie", "title": "Matrix"}).json()
    assert r["request"]["status"] == "sent" and r["message"] == "Demande envoyée à Radarr"


def test_radarr_failure_keeps_the_request_pending(fresh, monkeypatch):  # noqa: F811
    admin = client()
    setup_admin(admin)
    _configure()

    async def add_movie(self, tmdb_id, root_folder, quality_profile):
        raise ServiceError("radarr", "Radarr injoignable : ConnectError")

    monkeypatch.setattr(ArrClient, "add_movie", add_movie)
    r = admin.post("/api/requests", json={"tmdb_id": 603, "media_type": "movie", "title": "Matrix"}).json()
    assert r["request"]["status"] == "pending" and "gardée en attente" in r["message"]


# --- Réglages --------------------------------------------------------------------------------


def test_radarr_settings_are_for_the_main_admin_only(fresh, monkeypatch):  # noqa: F811
    admin = client()
    setup_admin(admin)
    assert admin.put("/api/settings", json={"radarr_url": "http://arr.test:7878", "radarr_api_key": "cle-secrete-radarr",
                                            "radarr_categories": [FILMS, DOCS, {**DOCS, "name": "Doublon"}]}).status_code == 200
    s = admin.get("/api/settings").json()
    assert s["radarr_url"] == "http://arr.test:7878" and [c["name"] for c in s["radarr_categories"]] == ["Films", "Reportages"]
    bad = {**FILMS, "rule": "inconnu"}
    assert admin.put("/api/settings", json={"radarr_categories": [bad]}).status_code == 422
    assert s["secrets"]["radarr_api_key"] == "••••darr" and "cle-secrete-radarr" not in str(s)
    status = admin.get("/api/settings/status").json()
    assert status["radarr"] == {"configured": True, "ready": True} and status["sonarr"]["configured"] is False

    async def options(self):
        return {"root_folders": [{"path": "/data/films", "free": 1}], "quality_profiles": [{"id": 4, "name": "HD"}]}

    monkeypatch.setattr(ArrClient, "options", options)
    assert admin.get("/api/arr/radarr/options").json()["quality_profiles"] == [{"id": 4, "name": "HD"}]

    member = add_member(admin)
    link_plex(member["id"])
    alice = login("alice", "provisoire1")
    alice.post("/api/auth/password", json={"new_password": "nouveau-mdp-alice"})
    assert alice.put("/api/settings", json={"radarr_url": "http://pirate.test"}).status_code == 403
    assert alice.put("/api/settings", json={"sonarr_categories": [FILMS]}).status_code == 403
    assert alice.get("/api/arr/radarr/options").status_code == 403
    assert alice.post("/api/settings/test/radarr").status_code == 403
    mine = alice.get("/api/settings").json()
    assert mine["radarr_url"] == "" and mine["radarr_categories"] == []
    st = alice.get("/api/settings/status").json()
    assert "radarr" not in st and "radarr" not in st["health"]


# --- Catégories ------------------------------------------------------------------------------


def test_category_suggested_from_tmdb_genres_and_keywords():
    from app.services import arr_categories as ac

    cats = [FILMS, DOCS, {**FILMS, "id": "concerts", "name": "Concerts", "rule": "music"},
            {**FILMS, "id": "spectacles", "name": "Spectacles", "rule": "standup"}]
    assert ac.suggest(cats, {"genres": [{"id": 28}]})["id"] == "films"
    assert ac.suggest(cats, {"genres": [{"id": 99}]})["id"] == "docs"
    assert ac.suggest(cats, {"genres": [{"id": 99}, {"id": 10402}]})["id"] == "concerts"  # film de concert
    assert ac.suggest(cats, {"genres": [{"id": 35}], "keywords": {"keywords": [{"id": 9716}]}})["id"] == "spectacles"
    # Sans catégorie « animes », un anime va dans « dessins animés » ; une catégorie incomplète n'est jamais proposée
    tv = [{**FILMS, "id": "series"}, {**FILMS, "id": "dessins", "rule": "animation"}, {**FILMS, "id": "vide", "rule": "anime", "quality_profile": 0}]
    assert ac.suggest(tv, {"genres": [{"id": 16}], "origin_country": ["JP"]})["id"] == "dessins"


def test_old_single_folder_setting_becomes_a_default_category():
    from app.services import arr_categories as ac

    class Old:
        radarr_categories = "[]"
        radarr_root_folder = "/data/films"
        radarr_quality_profile = 4

    assert ac.load(Old(), "radarr") == [{"id": "default", "name": "Films", "rule": "default", "root_folder": "/data/films", "quality_profile": 4}]


def test_admin_picks_another_category_and_quality_when_accepting(fresh, monkeypatch):  # noqa: F811
    admin = client()
    setup_admin(admin)
    _configure(radarr_categories=[FILMS, DOCS])
    added = []

    async def add_movie(self, tmdb_id, root_folder, quality_profile):
        added.append((root_folder, quality_profile))
        return "added"

    async def options(self):
        return {"root_folders": [], "quality_profiles": [{"id": 4, "name": "HD-1080p"}, {"id": 5, "name": "Ultra-HD"}]}

    monkeypatch.setattr(ArrClient, "add_movie", add_movie)
    monkeypatch.setattr(ArrClient, "options", options)
    alice, rid = _member_with_request(admin)
    opts = admin.get(f"/api/requests/{rid}/accept").json()
    assert opts["destination"] == "radarr" and opts["suggested"] == "films" and [c["id"] for c in opts["categories"]] == ["films", "docs"]
    assert [p["id"] for p in opts["quality_profiles"]] == [4, 5]
    assert alice.get(f"/api/requests/{rid}/accept").status_code == 403
    r = admin.post(f"/api/requests/{rid}/send", json={"category": "docs", "quality_profile": 5}).json()
    assert added == [("/data/docs", 5)] and r["sent_category"] == "Reportages"


def test_admin_own_request_uses_the_category_and_quality_chosen_on_the_title_page(fresh, monkeypatch):  # noqa: F811
    admin = client()
    setup_admin(admin)
    _configure(radarr_categories=[FILMS, DOCS])
    added = []

    async def add_movie(self, tmdb_id, root_folder, quality_profile):
        added.append((root_folder, quality_profile))
        return "added"

    async def options(self):
        return {"root_folders": [], "quality_profiles": [{"id": 4, "name": "HD-1080p"}, {"id": 5, "name": "Ultra-HD"}]}

    monkeypatch.setattr(ArrClient, "add_movie", add_movie)
    monkeypatch.setattr(ArrClient, "options", options)
    opts = admin.get("/api/requests/choices/movie/603").json()
    assert opts["destination"] == "radarr" and opts["suggested"] == "films" and len(opts["quality_profiles"]) == 2
    r = admin.post("/api/requests", json={"tmdb_id": 603, "media_type": "movie", "title": "Matrix", "category": "docs", "quality_profile": 5}).json()
    assert r["request"]["status"] == "sent" and r["request"]["sent_category"] == "Reportages" and added == [("/data/docs", 5)]

    # Un membre (même autorisé à demander sans validation) ne voit pas ces choix et ne peut pas les imposer
    member = add_member(admin)
    link_plex(member["id"])
    assert admin.patch(f"/api/admin/users/{member['id']}", json={"can_request_direct": True}).status_code == 200
    alice = login("alice", "provisoire1")
    alice.post("/api/auth/password", json={"new_password": "nouveau-mdp-alice"})
    assert alice.get("/api/requests/choices/movie/604").status_code == 403
    r = alice.post("/api/requests", json={"tmdb_id": 604, "media_type": "movie", "title": "Matrix 2", "category": "docs", "quality_profile": 5}).json()
    assert r["request"]["status"] == "sent" and added[-1] == ("/data/films", 4)
