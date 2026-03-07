"""
Brevo MCP Server (Remote) — Connecteur MCP hébergé pour Claude.ai

Version adaptée pour déploiement en ligne (Render, Railway, etc.)
Transport : Streamable HTTP

Configuration :
  Variables d'environnement requises :
    - BREVO_API_KEY : votre clé API Brevo
    - PORT : port du serveur (défaut: 8000, fourni automatiquement par Render)
"""

import json
import os
import sys
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

BREVO_API_BASE = "https://api.brevo.com/v3"
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

# ──────────────────────────────────────────────
# Server Initialization (Remote HTTP)
# ──────────────────────────────────────────────

mcp = FastMCP("brevo_mcp")


# ──────────────────────────────────────────────
# Shared Utilities
# ──────────────────────────────────────────────

class ResponseFormat(str, Enum):
    """Output format for tool responses."""
    MARKDOWN = "markdown"
    JSON = "json"


def _get_api_key() -> str:
    """Retrieve the Brevo API key from environment."""
    key = os.environ.get("BREVO_API_KEY", "")
    if not key:
        raise RuntimeError(
            "BREVO_API_KEY is not set. "
            "Please set it in your environment variables on your hosting platform. "
            "You can find your API key at https://app.brevo.com/settings/keys/api"
        )
    return key


def _headers() -> Dict[str, str]:
    """Standard headers for Brevo API requests."""
    return {
        "api-key": _get_api_key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _api_request(
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Centralized async HTTP client for Brevo API."""
    url = f"{BREVO_API_BASE}{path}"
    if params:
        params = {k: v for k, v in params.items() if v is not None}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=_headers(),
            json=body,
            params=params,
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.text:
            return {}
        return response.json()


def _handle_api_error(e: Exception) -> str:
    """Consistent, actionable error formatting."""
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        try:
            detail = e.response.json()
            msg = detail.get("message", str(detail))
        except Exception:
            msg = e.response.text or "Unknown error"

        error_map = {
            400: f"Requête invalide : {msg}. Vérifiez les paramètres envoyés.",
            401: "Clé API invalide. Vérifiez votre BREVO_API_KEY.",
            403: "Accès refusé. Votre clé API n'a pas les permissions nécessaires.",
            404: f"Ressource introuvable : {msg}. Vérifiez l'identifiant.",
            429: "Limite de requêtes atteinte. Attendez quelques secondes avant de réessayer.",
        }
        return error_map.get(status, f"Erreur API ({status}) : {msg}")
    elif isinstance(e, httpx.TimeoutException):
        return "Délai d'attente dépassé. Réessayez dans quelques instants."
    elif isinstance(e, RuntimeError):
        return str(e)
    return f"Erreur inattendue ({type(e).__name__}) : {e}"


def _format_contact_md(contact: Dict[str, Any]) -> str:
    """Format a single contact as Markdown."""
    email = contact.get("email", "N/A")
    attrs = contact.get("attributes", {})
    lines = [f"### {email}"]
    if attrs.get("PRENOM"):
        lines.append(f"- **Prénom** : {attrs['PRENOM']}")
    if attrs.get("NOM"):
        lines.append(f"- **Nom** : {attrs['NOM']}")
    for k, v in attrs.items():
        if k not in ("PRENOM", "NOM") and v:
            lines.append(f"- **{k}** : {v}")
    lists = contact.get("listIds", [])
    if lists:
        lines.append(f"- **Listes** : {', '.join(str(l) for l in lists)}")
    return "\n".join(lines)


def _format_campaign_md(campaign: Dict[str, Any]) -> str:
    """Format a single campaign as Markdown."""
    lines = [
        f"### {campaign.get('name', 'Sans nom')} (ID: {campaign.get('id', 'N/A')})",
        f"- **Objet** : {campaign.get('subject', 'N/A')}",
        f"- **Statut** : {campaign.get('status', 'N/A')}",
        f"- **Type** : {campaign.get('type', 'N/A')}",
    ]
    if campaign.get("scheduledAt"):
        lines.append(f"- **Programmée** : {campaign['scheduledAt']}")
    stats = campaign.get("statistics", {}).get("globalStats", {})
    if stats:
        lines.append(f"- **Envoyés** : {stats.get('sent', 0)}")
        lines.append(f"- **Ouverts** : {stats.get('viewed', 0)}")
        lines.append(f"- **Cliqués** : {stats.get('clickers', 0)}")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# CONTACTS — Tools
# ──────────────────────────────────────────────

class BrevoListContactsInput(BaseModel):
    """Input for listing contacts."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50, description="Nombre de contacts à retourner (1–50).")
    offset: int = Field(default=0, ge=0, description="Décalage pour la pagination.")
    modified_since: Optional[str] = Field(default=None, description="Filtrer les contacts modifiés depuis cette date (ISO 8601).")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Format de sortie.")


@mcp.tool(
    name="brevo_list_contacts",
    annotations={"title": "Lister les contacts Brevo", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_list_contacts(params: BrevoListContactsInput) -> str:
    """Liste les contacts de votre compte Brevo avec pagination."""
    try:
        query: Dict[str, Any] = {"limit": params.limit, "offset": params.offset}
        if params.modified_since:
            query["modifiedSince"] = params.modified_since
        data = await _api_request("GET", "/contacts", params=query)
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2, ensure_ascii=False)
        contacts = data.get("contacts", [])
        total = data.get("count", 0)
        lines = [f"# Contacts Brevo ({len(contacts)}/{total})", f"_Page : offset {params.offset}, limit {params.limit}_\n"]
        for c in contacts:
            lines.append(_format_contact_md(c))
            lines.append("")
        has_more = total > params.offset + len(contacts)
        if has_more:
            lines.append(f"_➡️ Plus de contacts disponibles (next offset: {params.offset + len(contacts)})_")
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)


class BrevoGetContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    identifier: str = Field(..., description="Email, ID numérique ou SMS du contact.", min_length=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Format de sortie.")


@mcp.tool(
    name="brevo_get_contact",
    annotations={"title": "Obtenir un contact Brevo", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_get_contact(params: BrevoGetContactInput) -> str:
    """Récupère les détails d'un contact Brevo par email ou ID."""
    try:
        data = await _api_request("GET", f"/contacts/{params.identifier}")
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2, ensure_ascii=False)
        return _format_contact_md(data)
    except Exception as e:
        return _handle_api_error(e)


class BrevoCreateContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    email: str = Field(..., description="Adresse email du contact.", pattern=r'^[\w\.\+\-]+@[\w\.\-]+\.\w+$')
    first_name: Optional[str] = Field(default=None, description="Prénom du contact.")
    last_name: Optional[str] = Field(default=None, description="Nom du contact.")
    attributes: Optional[Dict[str, Any]] = Field(default=None, description="Attributs personnalisés.")
    list_ids: Optional[List[int]] = Field(default=None, description="IDs des listes.")
    update_enabled: bool = Field(default=True, description="Mettre à jour si le contact existe déjà.")


@mcp.tool(
    name="brevo_create_contact",
    annotations={"title": "Créer un contact Brevo", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def brevo_create_contact(params: BrevoCreateContactInput) -> str:
    """Crée un nouveau contact dans Brevo ou le met à jour s'il existe déjà."""
    try:
        body: Dict[str, Any] = {"email": params.email, "updateEnabled": params.update_enabled}
        attrs = {}
        if params.first_name:
            attrs["PRENOM"] = params.first_name
        if params.last_name:
            attrs["NOM"] = params.last_name
        if params.attributes:
            attrs.update(params.attributes)
        if attrs:
            body["attributes"] = attrs
        if params.list_ids:
            body["listIds"] = params.list_ids
        data = await _api_request("POST", "/contacts", body=body)
        return f"✅ Contact créé/mis à jour.\n- **Email** : {params.email}\n- **ID** : {data.get('id', 'N/A')}"
    except Exception as e:
        return _handle_api_error(e)


class BrevoUpdateContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    identifier: str = Field(..., description="Email ou ID du contact.", min_length=1)
    attributes: Optional[Dict[str, Any]] = Field(default=None, description="Attributs à mettre à jour.")
    list_ids_to_add: Optional[List[int]] = Field(default=None, description="Listes à ajouter.")
    list_ids_to_remove: Optional[List[int]] = Field(default=None, description="Listes à retirer.")


@mcp.tool(
    name="brevo_update_contact",
    annotations={"title": "Mettre à jour un contact Brevo", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_update_contact(params: BrevoUpdateContactInput) -> str:
    """Met à jour les attributs ou listes d'un contact existant."""
    try:
        body: Dict[str, Any] = {}
        if params.attributes:
            body["attributes"] = params.attributes
        if params.list_ids_to_add:
            body["listIds"] = params.list_ids_to_add
        if params.list_ids_to_remove:
            body["unlinkListIds"] = params.list_ids_to_remove
        if not body:
            return "⚠️ Aucune modification spécifiée."
        await _api_request("PUT", f"/contacts/{params.identifier}", body=body)
        return f"✅ Contact **{params.identifier}** mis à jour."
    except Exception as e:
        return _handle_api_error(e)


class BrevoDeleteContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    identifier: str = Field(..., description="Email ou ID du contact à supprimer.", min_length=1)


@mcp.tool(
    name="brevo_delete_contact",
    annotations={"title": "Supprimer un contact Brevo", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
)
async def brevo_delete_contact(params: BrevoDeleteContactInput) -> str:
    """Supprime définitivement un contact. ⚠️ Irréversible."""
    try:
        await _api_request("DELETE", f"/contacts/{params.identifier}")
        return f"✅ Contact **{params.identifier}** supprimé."
    except Exception as e:
        return _handle_api_error(e)


# ──────────────────────────────────────────────
# LISTES — Tools
# ──────────────────────────────────────────────

class BrevoListListsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50, description="Nombre de listes.")
    offset: int = Field(default=0, ge=0, description="Décalage.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Format.")


@mcp.tool(
    name="brevo_list_lists",
    annotations={"title": "Lister les listes de contacts", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_list_lists(params: BrevoListListsInput) -> str:
    """Liste toutes les listes de contacts Brevo."""
    try:
        data = await _api_request("GET", "/contacts/lists", params={"limit": params.limit, "offset": params.offset})
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2, ensure_ascii=False)
        lists = data.get("lists", [])
        total = data.get("count", 0)
        lines = [f"# Listes de contacts ({len(lists)}/{total})\n"]
        for lst in lists:
            lines.append(f"- **{lst.get('name', 'Sans nom')}** (ID: {lst.get('id')}) — {lst.get('totalSubscribers', 0)} abonnés")
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)


class BrevoCreateListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="Nom de la liste.", min_length=1, max_length=200)
    folder_id: int = Field(..., description="ID du dossier.", ge=1)


@mcp.tool(
    name="brevo_create_list",
    annotations={"title": "Créer une liste", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def brevo_create_list(params: BrevoCreateListInput) -> str:
    """Crée une nouvelle liste de contacts."""
    try:
        data = await _api_request("POST", "/contacts/lists", body={"name": params.name, "folderId": params.folder_id})
        return f"✅ Liste **{params.name}** créée (ID: {data.get('id', 'N/A')})."
    except Exception as e:
        return _handle_api_error(e)


class BrevoGetListContactsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    list_id: int = Field(..., description="ID de la liste.", ge=1)
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50, description="Nombre de contacts.")
    offset: int = Field(default=0, ge=0, description="Décalage.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Format.")


@mcp.tool(
    name="brevo_get_list_contacts",
    annotations={"title": "Contacts d'une liste", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_get_list_contacts(params: BrevoGetListContactsInput) -> str:
    """Récupère les contacts d'une liste spécifique."""
    try:
        data = await _api_request("GET", f"/contacts/lists/{params.list_id}/contacts", params={"limit": params.limit, "offset": params.offset})
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2, ensure_ascii=False)
        contacts = data.get("contacts", [])
        total = data.get("count", 0)
        lines = [f"# Contacts de la liste {params.list_id} ({len(contacts)}/{total})\n"]
        for c in contacts:
            lines.append(_format_contact_md(c))
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)


# ──────────────────────────────────────────────
# CAMPAGNES EMAIL — Tools
# ──────────────────────────────────────────────

class BrevoListCampaignsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    status: Optional[str] = Field(default=None, description="Filtrer : 'draft', 'sent', 'queued', 'suspended', 'inProcess', 'archive'.")
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50, description="Nombre de campagnes.")
    offset: int = Field(default=0, ge=0, description="Décalage.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Format.")

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            valid = {"draft", "sent", "queued", "suspended", "inProcess", "archive"}
            if v not in valid:
                raise ValueError(f"Statut invalide '{v}'. Acceptés : {', '.join(sorted(valid))}")
        return v


@mcp.tool(
    name="brevo_list_campaigns",
    annotations={"title": "Lister les campagnes email", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_list_campaigns(params: BrevoListCampaignsInput) -> str:
    """Liste les campagnes email avec pagination et filtrage par statut."""
    try:
        query: Dict[str, Any] = {"limit": params.limit, "offset": params.offset}
        if params.status:
            query["status"] = params.status
        data = await _api_request("GET", "/emailCampaigns", params=query)
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2, ensure_ascii=False)
        campaigns = data.get("campaigns", [])
        total = data.get("count", 0)
        lines = [f"# Campagnes Email ({len(campaigns)}/{total})\n"]
        for c in campaigns:
            lines.append(_format_campaign_md(c))
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)


class BrevoGetCampaignInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    campaign_id: int = Field(..., description="ID de la campagne.", ge=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Format.")


@mcp.tool(
    name="brevo_get_campaign",
    annotations={"title": "Détails d'une campagne", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_get_campaign(params: BrevoGetCampaignInput) -> str:
    """Récupère les détails complets d'une campagne email."""
    try:
        data = await _api_request("GET", f"/emailCampaigns/{params.campaign_id}")
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2, ensure_ascii=False)
        return _format_campaign_md(data)
    except Exception as e:
        return _handle_api_error(e)


class BrevoCreateCampaignInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="Nom interne de la campagne.", min_length=1, max_length=200)
    subject: str = Field(..., description="Objet de l'email.", min_length=1, max_length=200)
    sender_name: str = Field(..., description="Nom de l'expéditeur.", min_length=1)
    sender_email: str = Field(..., description="Email expéditeur (vérifié dans Brevo).", pattern=r'^[\w\.\+\-]+@[\w\.\-]+\.\w+$')
    html_content: str = Field(..., description="Contenu HTML avec {{ unsubscribe }}.", min_length=10)
    list_ids: List[int] = Field(..., description="IDs des listes destinataires.", min_length=1)
    reply_to: Optional[str] = Field(default=None, description="Email de réponse.")
    scheduled_at: Optional[str] = Field(default=None, description="Date d'envoi programmé (ISO 8601).")


@mcp.tool(
    name="brevo_create_campaign",
    annotations={"title": "Créer une campagne email", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def brevo_create_campaign(params: BrevoCreateCampaignInput) -> str:
    """Crée une campagne email (brouillon ou programmée)."""
    try:
        body: Dict[str, Any] = {
            "name": params.name, "subject": params.subject,
            "sender": {"name": params.sender_name, "email": params.sender_email},
            "htmlContent": params.html_content, "recipients": {"listIds": params.list_ids},
        }
        if params.reply_to:
            body["replyTo"] = params.reply_to
        if params.scheduled_at:
            body["scheduledAt"] = params.scheduled_at
        data = await _api_request("POST", "/emailCampaigns", body=body)
        status = "programmée" if params.scheduled_at else "brouillon"
        return f"✅ Campagne créée.\n- **Nom** : {params.name}\n- **ID** : {data.get('id', 'N/A')}\n- **Statut** : {status}"
    except Exception as e:
        return _handle_api_error(e)


class BrevoSendCampaignInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    campaign_id: int = Field(..., description="ID de la campagne à envoyer.", ge=1)


@mcp.tool(
    name="brevo_send_campaign",
    annotations={"title": "Envoyer une campagne", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def brevo_send_campaign(params: BrevoSendCampaignInput) -> str:
    """Envoie immédiatement une campagne email. ⚠️ Irréversible."""
    try:
        await _api_request("POST", f"/emailCampaigns/{params.campaign_id}/sendNow")
        return f"✅ Campagne **{params.campaign_id}** envoyée !"
    except Exception as e:
        return _handle_api_error(e)


class BrevoSendTestCampaignInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    campaign_id: int = Field(..., description="ID de la campagne.", ge=1)
    email_to: List[str] = Field(..., description="Emails destinataires du test (max 5).", min_length=1, max_length=5)


@mcp.tool(
    name="brevo_send_test_campaign",
    annotations={"title": "Test de campagne", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_send_test_campaign(params: BrevoSendTestCampaignInput) -> str:
    """Envoie un email de test pour prévisualiser une campagne."""
    try:
        await _api_request("POST", f"/emailCampaigns/{params.campaign_id}/sendTest", body={"emailTo": params.email_to})
        return f"✅ Test envoyé à : {', '.join(params.email_to)}"
    except Exception as e:
        return _handle_api_error(e)


# ──────────────────────────────────────────────
# STATISTIQUES — Tools
# ──────────────────────────────────────────────

class BrevoGetCampaignStatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    campaign_id: int = Field(..., description="ID de la campagne.", ge=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Format.")


@mcp.tool(
    name="brevo_get_campaign_stats",
    annotations={"title": "Stats d'une campagne", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_get_campaign_stats(params: BrevoGetCampaignStatsInput) -> str:
    """Statistiques détaillées d'une campagne : envois, ouvertures, clics, taux."""
    try:
        data = await _api_request("GET", f"/emailCampaigns/{params.campaign_id}")
        if params.response_format == ResponseFormat.JSON:
            stats = data.get("statistics", {})
            stats["campaignName"] = data.get("name")
            return json.dumps(stats, indent=2, ensure_ascii=False)
        name = data.get("name", "Sans nom")
        subject = data.get("subject", "N/A")
        stats = data.get("statistics", {}).get("globalStats", {})
        sent = stats.get("sent", 0)
        delivered = stats.get("delivered", 0)
        viewed = stats.get("viewed", 0)
        unique_views = stats.get("uniqueViews", 0)
        clickers = stats.get("clickers", 0)
        unique_clicks = stats.get("uniqueClicks", 0)
        unsubscriptions = stats.get("unsubscriptions", 0)
        hard_bounces = stats.get("hardBounces", 0)
        soft_bounces = stats.get("softBounces", 0)
        spam = stats.get("complaints", 0)
        delivery_rate = (delivered / sent * 100) if sent > 0 else 0
        open_rate = (unique_views / delivered * 100) if delivered > 0 else 0
        click_rate = (unique_clicks / delivered * 100) if delivered > 0 else 0
        unsub_rate = (unsubscriptions / delivered * 100) if delivered > 0 else 0
        lines = [
            f"# 📊 Statistiques : {name}", f"**Objet** : {subject}\n",
            "## Envoi",
            f"- Envoyés : **{sent}**", f"- Délivrés : **{delivered}** ({delivery_rate:.1f}%)",
            f"- Hard bounces : {hard_bounces}", f"- Soft bounces : {soft_bounces}\n",
            "## Engagement",
            f"- Ouvertures uniques : **{unique_views}** ({open_rate:.1f}%)",
            f"- Clics uniques : **{unique_clicks}** ({click_rate:.1f}%)\n",
            "## Désengagement",
            f"- Désabonnements : {unsubscriptions} ({unsub_rate:.1f}%)", f"- Plaintes spam : {spam}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)


class BrevoGetAccountInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Format.")


@mcp.tool(
    name="brevo_get_account",
    annotations={"title": "Infos du compte Brevo", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_get_account(params: BrevoGetAccountInput) -> str:
    """Informations du compte Brevo : plan, crédits, limites."""
    try:
        data = await _api_request("GET", "/account")
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2, ensure_ascii=False)
        plan = data.get("plan", [{}])
        plan_info = plan[0] if plan else {}
        lines = [
            "# 🏢 Compte Brevo\n",
            f"- **Entreprise** : {data.get('companyName', 'N/A')}",
            f"- **Email** : {data.get('email', 'N/A')}",
            f"- **Plan** : {plan_info.get('type', 'N/A')}",
            f"- **Crédits** : {plan_info.get('credits', 'N/A')}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)


# ──────────────────────────────────────────────
# EMAILS TRANSACTIONNELS — Tools
# ──────────────────────────────────────────────

class BrevoSendTransactionalInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    to_email: str = Field(..., description="Email du destinataire.", pattern=r'^[\w\.\+\-]+@[\w\.\-]+\.\w+$')
    to_name: Optional[str] = Field(default=None, description="Nom du destinataire.")
    sender_email: str = Field(..., description="Email expéditeur (vérifié).", pattern=r'^[\w\.\+\-]+@[\w\.\-]+\.\w+$')
    sender_name: str = Field(..., description="Nom de l'expéditeur.", min_length=1)
    subject: str = Field(..., description="Objet.", min_length=1, max_length=200)
    html_content: str = Field(..., description="Contenu HTML.", min_length=1)
    reply_to_email: Optional[str] = Field(default=None, description="Email de réponse.")
    tags: Optional[List[str]] = Field(default=None, description="Tags de catégorisation.", max_length=10)


@mcp.tool(
    name="brevo_send_transactional",
    annotations={"title": "Email transactionnel", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def brevo_send_transactional(params: BrevoSendTransactionalInput) -> str:
    """Envoie un email transactionnel individuel (confirmation, notification…)."""
    try:
        to_entry: Dict[str, str] = {"email": params.to_email}
        if params.to_name:
            to_entry["name"] = params.to_name
        body: Dict[str, Any] = {
            "sender": {"name": params.sender_name, "email": params.sender_email},
            "to": [to_entry], "subject": params.subject, "htmlContent": params.html_content,
        }
        if params.reply_to_email:
            body["replyTo"] = {"email": params.reply_to_email}
        if params.tags:
            body["tags"] = params.tags
        data = await _api_request("POST", "/smtp/email", body=body)
        return f"✅ Email envoyé à {params.to_email}.\n- **Message ID** : {data.get('messageId', 'N/A')}"
    except Exception as e:
        return _handle_api_error(e)


class BrevoSendTemplateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    template_id: int = Field(..., description="ID du template Brevo.", ge=1)
    to_email: str = Field(..., description="Email du destinataire.", pattern=r'^[\w\.\+\-]+@[\w\.\-]+\.\w+$')
    to_name: Optional[str] = Field(default=None, description="Nom du destinataire.")
    params: Optional[Dict[str, str]] = Field(default=None, description="Variables dynamiques du template.")
    tags: Optional[List[str]] = Field(default=None, description="Tags.")


@mcp.tool(
    name="brevo_send_template",
    annotations={"title": "Email depuis template", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def brevo_send_template(params: BrevoSendTemplateInput) -> str:
    """Envoie un email basé sur un template Brevo pré-configuré."""
    try:
        to_entry: Dict[str, str] = {"email": params.to_email}
        if params.to_name:
            to_entry["name"] = params.to_name
        body: Dict[str, Any] = {"templateId": params.template_id, "to": [to_entry]}
        if params.params:
            body["params"] = params.params
        if params.tags:
            body["tags"] = params.tags
        data = await _api_request("POST", "/smtp/email", body=body)
        return f"✅ Template #{params.template_id} envoyé à {params.to_email}.\n- **Message ID** : {data.get('messageId', 'N/A')}"
    except Exception as e:
        return _handle_api_error(e)


class BrevoGetTransactionalStatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    days: int = Field(default=7, ge=1, le=90, description="Jours d'historique (1-90).")
    tag: Optional[str] = Field(default=None, description="Filtrer par tag.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Format.")


@mcp.tool(
    name="brevo_get_transactional_stats",
    annotations={"title": "Stats transactionnels", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_get_transactional_stats(params: BrevoGetTransactionalStatsInput) -> str:
    """Statistiques agrégées des emails transactionnels."""
    try:
        query: Dict[str, Any] = {"days": params.days}
        if params.tag:
            query["tag"] = params.tag
        data = await _api_request("GET", "/smtp/statistics/aggregatedReport", params=query)
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2, ensure_ascii=False)
        lines = [
            f"# 📊 Stats transactionnels ({params.days} jours)\n",
            f"- **Requêtes** : {data.get('requests', 0)}", f"- **Délivrés** : {data.get('delivered', 0)}",
            f"- **Ouvertures** : {data.get('opens', 0)}", f"- **Clics** : {data.get('clicks', 0)}",
            f"- **Hard bounces** : {data.get('hardBounces', 0)}", f"- **Soft bounces** : {data.get('softBounces', 0)}",
            f"- **Spam** : {data.get('spamReports', 0)}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)


# ──────────────────────────────────────────────
# DOSSIERS — Tools
# ──────────────────────────────────────────────

class BrevoListFoldersInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50, description="Nombre de dossiers.")
    offset: int = Field(default=0, ge=0, description="Décalage.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Format.")


@mcp.tool(
    name="brevo_list_folders",
    annotations={"title": "Lister les dossiers", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def brevo_list_folders(params: BrevoListFoldersInput) -> str:
    """Liste les dossiers de contacts Brevo."""
    try:
        data = await _api_request("GET", "/contacts/folders", params={"limit": params.limit, "offset": params.offset})
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2, ensure_ascii=False)
        folders = data.get("folders", [])
        total = data.get("count", 0)
        lines = [f"# Dossiers ({len(folders)}/{total})\n"]
        for f in folders:
            lines.append(f"- **{f.get('name', 'Sans nom')}** (ID: {f.get('id')}) — {f.get('totalSubscribers', 0)} contacts")
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)


# ──────────────────────────────────────────────
# Entry Point — Remote Streamable HTTP
# ──────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
