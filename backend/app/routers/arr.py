"""Radarr / Sonarr : choix du dossier de destination et de la qualité (administrateur principal).

Les demandes acceptées y sont envoyées par /requests ; ce routeur ne sert qu'aux réglages.
"""

from typing import Literal

from fastapi import APIRouter, Depends

from app.services.arr_client import ArrClient
from app.services.context import Ctx, require_server_admin
from app.services.errors import NotConfigured

router = APIRouter(prefix="/arr", tags=["arr"])


@router.get("/{kind}/options")
async def options(kind: Literal["radarr", "sonarr"], ctx: Ctx = Depends(require_server_admin)):
    s = ctx.settings
    url, key = getattr(s, f"{kind}_url"), getattr(s, f"{kind}_api_key")
    if not (url and key):
        raise NotConfigured(kind, "Enregistre d'abord l'adresse et la clé API")
    return await ArrClient(kind, url, key).options()
