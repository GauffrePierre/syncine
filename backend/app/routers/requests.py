"""Demandes d'ajout à la bibliothèque Plex.

- Admin, ou membre autorisé par l'admin : acceptée tout de suite, envoyée à Radarr (film) ou Sonarr (série) s'ils
  sont réglés, sinon à Seerr.
- Sinon : file d'attente ; l'administrateur l'accepte (même envoi) ou la refuse dans SynCiné (onglet Demandes).
Chaque membre voit ses demandes ; l'admin les voit toutes.
"""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.models import MediaRequest, Notification, User
from app.services import arr_categories, messages
from app.services.context import Ctx, get_ctx
from app.services.errors import NotConfigured, ServiceError

router = APIRouter(prefix="/requests", tags=["requests"])

OPEN = ("pending", "sent")
MAX_PENDING = 50  # demandes en attente par membre (la file et la cloche de l'administrateur ne se remplissent pas sans fin)
_TMDB_POSTER = re.compile(r"https://image\.tmdb\.org/t/p/\w+/[\w-]+\.(?:jpg|png|webp)")


class RequestIn(BaseModel):
    """Le contenu vient du navigateur du membre et s'affiche chez l'administrateur : tout est borné."""

    tmdb_id: int = Field(gt=0)
    media_type: Literal["movie", "tv"]
    title: str = Field("", max_length=300)
    year: int | None = Field(None, ge=1870, le=2200)
    poster: str | None = Field(None, max_length=300)
    seasons: list[int] | None = Field(None, max_length=100)
    note: str = Field("", max_length=500)
    # Demande de l'administrateur, envoyée tout de suite : catégorie et qualité Radarr / Sonarr (ignorées sinon)
    category: str | None = Field(None, max_length=40)
    quality_profile: int | None = Field(None, gt=0)

    @field_validator("poster")
    @classmethod
    def tmdb_poster_only(cls, v: str | None) -> str | None:
        # Affiche TMDB seulement : l'administrateur ne charge jamais une adresse choisie par un membre
        return v if v and _TMDB_POSTER.fullmatch(v) else None


class RequestUpdate(BaseModel):
    status: Literal["pending", "declined", "available"]
    note: str | None = Field(None, max_length=500)


def find_open(ctx: Ctx, media_type: str, tmdb_id: int) -> MediaRequest | None:
    """Demande en cours pour ce titre, quel que soit le compte (pas de doublon dans la file)."""
    return ctx.db.execute(
        select(MediaRequest)
        .where(MediaRequest.tmdb_id == tmdb_id, MediaRequest.media_type == media_type, MediaRequest.status.in_(OPEN))
        .order_by(MediaRequest.id)
        .limit(1)
        .execution_options(all_users=True)
    ).scalar_one_or_none()


def visible(ctx: Ctx, req: MediaRequest | None) -> dict | None:
    """Demande telle qu'un compte peut la voir : en entier si c'est la sienne (ou pour l'admin),
    sinon seulement son état (« déjà demandé »), sans la note ni le nom de l'autre membre."""
    if req is None:
        return None
    if ctx.is_admin or req.user_id == ctx.user.id:
        return _for(ctx, req.to_dict())
    return {"id": None, "tmdb_id": req.tmdb_id, "media_type": req.media_type, "status": req.status, "mine": False}


def _get(ctx: Ctx, request_id: int, admin: bool = True) -> MediaRequest:
    # db.get passe aussi par le filtre par compte : l'admin doit pouvoir lire les demandes de tous
    req = ctx.db.get(MediaRequest, request_id, execution_options={"all_users": ctx.is_admin})
    if req is None or (req.user_id != ctx.user.id and not ctx.is_admin):
        raise HTTPException(404, "Demande introuvable")
    if admin and not ctx.is_admin:
        raise HTTPException(403, "Réservé à l'administrateur")
    return req


def _names(ctx: Ctx) -> dict[int, str]:
    return {u.id: u.name for u in ctx.db.query(User).all()}


def _for(ctx: Ctx, d: dict) -> dict:
    """L'adresse de Seerr (réseau du serveur) n'est montrée qu'à l'admin."""
    return d if ctx.server_admin else {**d, "seerr_url": None}


def _out(ctx: Ctx, req: MediaRequest, names: dict[int, str]) -> dict:
    return _for(ctx, {**req.to_dict(), "user_name": names.get(req.user_id, "")})


def destination(ctx: Ctx, media_type: str) -> str | None:
    """Où part une demande acceptée : Radarr (films) / Sonarr (séries) s'ils sont prêts, sinon Seerr."""
    arr = ctx.radarr if media_type == "movie" else ctx.sonarr
    if arr is not None and arr.ready:
        return arr.kind
    return "seerr" if ctx.seerr is not None else None


def can_send_direct(ctx: Ctx, media_type: str = "movie") -> bool:
    return destination(ctx, media_type) is not None and (ctx.is_admin or ctx.user.can_request_direct)


def _notify(ctx: Ctx, user_id: int, req: MediaRequest, key: str, **params) -> None:
    ctx.db.add(Notification(
        user_id=user_id, kind="request", tmdb_id=req.tmdb_id, media_type=req.media_type, title=req.title, poster=req.poster,
        **messages.fields(key, title=req.title, **params),
    ))


SENT_MESSAGES = {"radarr": "Demande envoyée à Radarr", "sonarr": "Demande envoyée à Sonarr", "seerr": "Demande envoyée à Seerr"}


class AcceptIn(BaseModel):
    """Choix de l'administrateur en acceptant : catégorie et qualité (sinon celles proposées)."""

    category: str | None = Field(None, max_length=40)
    quality_profile: int | None = Field(None, gt=0)


async def _tmdb_raw(ctx: Ctx, media_type: str, tmdb_id: int) -> dict:
    """Fiche TMDB complète (genres, mots-clés, identifiant TVDB) ; vide si TMDB ne répond pas."""
    if getattr(ctx.tmdb, "is_mock", False):
        return {}  # sans clé TMDB : catégorie par défaut, et pas d'identifiant TVDB pour Sonarr
    try:
        return await ctx.tmdb.detail_raw(media_type, tmdb_id) or {}
    except ServiceError:
        return {}


async def _choices(ctx: Ctx, media_type: str, tmdb_id: int) -> dict:
    """Destination, catégories (celle proposée pour ce titre) et qualités possibles, avant l'envoi."""
    dest = destination(ctx, media_type)
    if dest in (None, "seerr"):
        return {"destination": dest}
    arr = ctx.radarr if dest == "radarr" else ctx.sonarr
    cats = arr_categories.complete(arr.categories)
    suggested = arr_categories.suggest(cats, await _tmdb_raw(ctx, media_type, tmdb_id))
    try:
        profiles = (await arr.options())["quality_profiles"]
    except ServiceError:
        profiles = []  # liste indisponible : la qualité de la catégorie reste utilisable
    return {
        "destination": dest,
        "categories": [{"id": c["id"], "name": c["name"], "quality_profile": c["quality_profile"]} for c in cats],
        "suggested": suggested["id"] if suggested else None,
        "quality_profiles": profiles,
    }


async def _send(ctx: Ctx, req: MediaRequest, seasons: list[int] | None = None, choice: AcceptIn | None = None) -> str:
    """Envoie la demande acceptée ; renvoie sa destination. Radarr qui a déjà le fichier : disponible tout de suite."""
    dest = destination(ctx, req.media_type)
    if dest is None:
        if req.media_type == "movie":
            raise NotConfigured("radarr", "Ni Radarr ni Seerr ne sont prêts : règle-les dans Profil → Connexions")
        raise NotConfigured("sonarr", "Ni Sonarr ni Seerr ne sont prêts : règle-les dans Profil → Connexions")
    if dest == "seerr":
        res = await ctx.seerr.request(req.media_type, req.tmdb_id, seasons)
        req.seerr_url = res.get("url")
        state = "sent"
    else:
        arr = ctx.radarr if dest == "radarr" else ctx.sonarr
        raw = await _tmdb_raw(ctx, req.media_type, req.tmdb_id)
        cat = arr_categories.find(arr.categories, choice and choice.category) or arr_categories.suggest(arr.categories, raw)
        profile = (choice and choice.quality_profile) or cat["quality_profile"]
        if dest == "radarr":
            state = await arr.add_movie(req.tmdb_id, cat["root_folder"], profile)
        else:
            tvdb_id = raw.get("external_ids", {}).get("tvdb_id")
            if not tvdb_id:
                raise ServiceError("sonarr", "Série sans identifiant TVDB : Sonarr ne peut pas l'ajouter", 422)
            series_type = "anime" if cat.get("rule") == "anime" else "standard"
            state = await arr.add_series(int(tvdb_id), cat["root_folder"], profile, seasons, series_type)
        req.sent_category = cat["name"]
    req.status = "available" if state == "available" else "sent"
    req.sent_to = dest
    return dest


@router.get("")
def list_requests(status: str | None = None, scope: Literal["mine", "all"] = "mine", ctx: Ctx = Depends(get_ctx)):
    if scope == "all" and not ctx.is_admin:
        raise HTTPException(403, "Réservé à l'administrateur")
    q = select(MediaRequest).order_by(MediaRequest.created_at.desc(), MediaRequest.id.desc())
    if status:
        q = q.where(MediaRequest.status == status)
    if scope == "all":
        q = q.execution_options(all_users=True)
    rows = ctx.db.execute(q).scalars().all()
    names = _names(ctx)
    return {
        "results": [_out(ctx, r, names) for r in rows],
        "pending": sum(1 for r in rows if r.status == "pending"),
        "seerr": ctx.seerr is not None,
        # Destination d'une demande acceptée, par type (bouton « Accepter » de l'administrateur)
        "send_to": {"movie": destination(ctx, "movie"), "tv": destination(ctx, "tv")},
        "direct": can_send_direct(ctx, "movie") or can_send_direct(ctx, "tv"),
        "allowed": can_request(ctx),
    }


def can_request(ctx: Ctx) -> bool:
    """Une demande ajoute un titre au serveur multimédia : réservée à l'admin et aux comptes reliés au serveur
    (Plex, Jellyfin ou Emby)."""
    return ctx.is_admin or ctx.media is not None


@router.post("")
async def create_request(body: RequestIn, ctx: Ctx = Depends(get_ctx)):
    if not can_request(ctx):
        raise HTTPException(403, "Les demandes d'ajout sont réservées aux comptes reliés au serveur multimédia")
    existing = find_open(ctx, body.media_type, body.tmdb_id)
    if existing:
        return {"request": visible(ctx, existing), "created": False}
    if not ctx.is_admin:
        q = select(func.count(MediaRequest.id)).where(MediaRequest.user_id == ctx.user.id, MediaRequest.status == "pending")
        if ctx.db.execute(q).scalar_one() >= MAX_PENDING:
            raise HTTPException(429, "Trop de demandes en attente : attends que l'administrateur les traite")
    req = MediaRequest(
        tmdb_id=body.tmdb_id, media_type=body.media_type, title=body.title, year=body.year, poster=body.poster, note=body.note,
        requested_by=ctx.user.username, status="pending",
    )
    ctx.db.add(req)
    message = "Demande enregistrée — l'administrateur la traitera"
    if can_send_direct(ctx, body.media_type):
        try:
            choice = AcceptIn(category=body.category, quality_profile=body.quality_profile) if ctx.is_admin else None
            message = SENT_MESSAGES[await _send(ctx, req, body.seasons, choice)]
        except ServiceError as e:
            # On garde la demande en attente plutôt que de la perdre
            req.status = "pending"
            message = f"{e.message} — demande gardée en attente"
    if req.status == "pending" and not ctx.is_admin:
        # Prévient les administrateurs (cloche) qu'une demande attend
        for admin in ctx.db.query(User).filter(User.role == "admin", User.active.is_(True)):
            _notify(ctx, admin.id, req, "{name} demande « {title} »", name=ctx.user.name)
    ctx.db.commit()
    return {"request": _out(ctx, req, _names(ctx)), "created": True, "message": message}


@router.get("/choices/{media_type}/{tmdb_id}")
async def new_request_choices(media_type: Literal["movie", "tv"], tmdb_id: int, ctx: Ctx = Depends(get_ctx)):
    """Demande de l'administrateur depuis une fiche : choix de la catégorie et de la qualité avant l'envoi."""
    if not ctx.is_admin:
        raise HTTPException(403, "Réservé à l'administrateur")
    return await _choices(ctx, media_type, tmdb_id)


@router.get("/{request_id}/accept")
async def accept_options(request_id: int, ctx: Ctx = Depends(get_ctx)):
    """Avant d'accepter : destination, catégories (celle proposée pour ce titre) et qualités possibles."""
    req = _get(ctx, request_id)
    return await _choices(ctx, req.media_type, req.tmdb_id)


@router.post("/{request_id}/send")
async def send_request(request_id: int, body: AcceptIn | None = None, ctx: Ctx = Depends(get_ctx)):
    """L'administrateur accepte la demande : envoi à Radarr / Sonarr (catégorie et qualité choisies) ou à Seerr."""
    req = _get(ctx, request_id)
    if req.status != "pending":
        raise HTTPException(409, "Cette demande a déjà été traitée")
    dest = await _send(ctx, req, choice=body)
    if req.user_id != ctx.user.id:
        if dest == "seerr":
            _notify(ctx, req.user_id, req, "Ta demande « {title} » a été envoyée à Seerr")
        else:
            _notify(ctx, req.user_id, req, "Ta demande « {title} » a été acceptée")
    ctx.db.commit()
    return _out(ctx, req, _names(ctx))


@router.patch("/{request_id}")
def update_request(request_id: int, body: RequestUpdate, ctx: Ctx = Depends(get_ctx)):
    req = _get(ctx, request_id)
    changed = req.status != body.status
    req.status = body.status
    if body.note is not None:
        req.note = body.note
    if changed and req.user_id != ctx.user.id and body.status in ("available", "declined"):
        if body.status == "available":
            # Nom du serveur de la bibliothèque (Plex, Jellyfin ou Emby)
            server = "Plex" if ctx.plex is not None else (ctx.jellyfin.label if getattr(ctx, "jellyfin", None) else "Plex")
            _notify(ctx, req.user_id, req, "Ta demande « {title} » est disponible sur {server}", server=server)
        else:
            _notify(ctx, req.user_id, req, "Ta demande « {title} » a été refusée")
    ctx.db.commit()
    return _out(ctx, req, _names(ctx))


@router.delete("/{request_id}")
def delete_request(request_id: int, ctx: Ctx = Depends(get_ctx)):
    req = _get(ctx, request_id, admin=False)
    if not ctx.is_admin and req.status != "pending":
        raise HTTPException(403, "Seules les demandes en attente peuvent être retirées")
    ctx.db.delete(req)
    ctx.db.commit()
    return {"ok": True}
