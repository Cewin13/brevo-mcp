"""
Brevo MCP Server (Remote) — Connecteur MCP pour Claude.ai
Utilise fastmcp (standalone) + Streamable HTTP.
Variable d'environnement requise : BREVO_API_KEY
"""

import json
import os
from typing import Any, Dict, List, Optional

import httpx
from fastmcp import FastMCP

# ──────────────────────────────────────────────
BREVO_API_BASE = "https://api.brevo.com/v3"

mcp = FastMCP(
    "brevo_mcp",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 10000)),
    stateless_http=True,
    json_response=True,
)

# ──────────────────────────────────────────────
# Shared Utilities
# ──────────────────────────────────────────────

def _get_api_key() -> str:
    key = os.environ.get("BREVO_API_KEY", "")
    if not key:
        raise RuntimeError("BREVO_API_KEY non définie.")
    return key

def _headers() -> Dict[str, str]:
    return {"api-key": _get_api_key(), "Content-Type": "application/json", "Accept": "application/json"}

async def _api_request(method: str, path: str, body: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{BREVO_API_BASE}{path}"
    if params:
        params = {k: v for k, v in params.items() if v is not None}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(method=method, url=url, headers=_headers(), json=body, params=params)
        response.raise_for_status()
        if response.status_code == 204 or not response.text:
            return {}
        return response.json()

def _handle_api_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        try:
            msg = e.response.json().get("message", e.response.text)
        except Exception:
            msg = e.response.text or "Erreur inconnue"
        error_map = {400: f"Requête invalide : {msg}", 401: "Clé API invalide.", 403: "Accès refusé.", 404: f"Introuvable : {msg}", 429: "Limite atteinte."}
        return error_map.get(status, f"Erreur API ({status}) : {msg}")
    elif isinstance(e, httpx.TimeoutException):
        return "Délai dépassé."
    elif isinstance(e, RuntimeError):
        return str(e)
    return f"Erreur ({type(e).__name__}) : {e}"

def _format_contact_md(contact: Dict[str, Any]) -> str:
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
    lines = [
        f"### {campaign.get('name', 'Sans nom')} (ID: {campaign.get('id', 'N/A')})",
        f"- **Objet** : {campaign.get('subject', 'N/A')}",
        f"- **Statut** : {campaign.get('status', 'N/A')}",
    ]
    stats = campaign.get("statistics", {}).get("globalStats", {})
    if stats:
        lines.append(f"- **Envoyés** : {stats.get('sent', 0)}")
        lines.append(f"- **Ouverts** : {stats.get('viewed', 0)}")
        lines.append(f"- **Cliqués** : {stats.get('clickers', 0)}")
    return "\n".join(lines)

# ──────────────────────────────────────────────
# CONTACTS
# ──────────────────────────────────────────────

@mcp.tool(name="brevo_list_contacts")
async def brevo_list_contacts(limit: int = 20, offset: int = 0) -> str:
    """Liste les contacts Brevo avec pagination. Paramètres: limit (1-50), offset."""
    try:
        data = await _api_request("GET", "/contacts", params={"limit": limit, "offset": offset})
        contacts = data.get("contacts", [])
        total = data.get("count", 0)
        lines = [f"# Contacts Brevo ({len(contacts)}/{total})\n"]
        for c in contacts:
            lines.append(_format_contact_md(c))
            lines.append("")
        if total > offset + len(contacts):
            lines.append(f"_Plus disponibles (next offset: {offset + len(contacts)})_")
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_get_contact")
async def brevo_get_contact(identifier: str) -> str:
    """Récupère un contact par email ou ID numérique."""
    try:
        data = await _api_request("GET", f"/contacts/{identifier}")
        return _format_contact_md(data)
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_create_contact")
async def brevo_create_contact(email: str, first_name: str = "", last_name: str = "", list_ids: str = "") -> str:
    """Crée ou met à jour un contact. list_ids: IDs séparés par virgules (ex: '2,5')."""
    try:
        body: Dict[str, Any] = {"email": email, "updateEnabled": True}
        attrs = {}
        if first_name:
            attrs["PRENOM"] = first_name
        if last_name:
            attrs["NOM"] = last_name
        if attrs:
            body["attributes"] = attrs
        if list_ids:
            body["listIds"] = [int(x.strip()) for x in list_ids.split(",") if x.strip()]
        data = await _api_request("POST", "/contacts", body=body)
        return f"Contact créé/mis à jour. Email: {email}, ID: {data.get('id', 'N/A')}"
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_update_contact")
async def brevo_update_contact(identifier: str, attributes_json: str = "", list_ids_to_add: str = "", list_ids_to_remove: str = "") -> str:
    """Met à jour un contact. attributes_json: JSON des attributs. list_ids_to_add/remove: IDs séparés par virgules."""
    try:
        body: Dict[str, Any] = {}
        if attributes_json:
            body["attributes"] = json.loads(attributes_json)
        if list_ids_to_add:
            body["listIds"] = [int(x.strip()) for x in list_ids_to_add.split(",") if x.strip()]
        if list_ids_to_remove:
            body["unlinkListIds"] = [int(x.strip()) for x in list_ids_to_remove.split(",") if x.strip()]
        if not body:
            return "Aucune modification spécifiée."
        await _api_request("PUT", f"/contacts/{identifier}", body=body)
        return f"Contact {identifier} mis à jour."
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_delete_contact")
async def brevo_delete_contact(identifier: str) -> str:
    """Supprime un contact (irréversible). identifier: email ou ID."""
    try:
        await _api_request("DELETE", f"/contacts/{identifier}")
        return f"Contact {identifier} supprimé."
    except Exception as e:
        return _handle_api_error(e)

# ──────────────────────────────────────────────
# LISTES & DOSSIERS
# ──────────────────────────────────────────────

@mcp.tool(name="brevo_list_lists")
async def brevo_list_lists(limit: int = 20, offset: int = 0) -> str:
    """Liste les listes de contacts Brevo."""
    try:
        data = await _api_request("GET", "/contacts/lists", params={"limit": limit, "offset": offset})
        lists = data.get("lists", [])
        total = data.get("count", 0)
        lines = [f"# Listes ({len(lists)}/{total})\n"]
        for lst in lists:
            lines.append(f"- **{lst.get('name', 'Sans nom')}** (ID: {lst.get('id')}) — {lst.get('totalSubscribers', 0)} abonnés")
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_create_list")
async def brevo_create_list(name: str, folder_id: int) -> str:
    """Crée une liste de contacts. Paramètres: name, folder_id."""
    try:
        data = await _api_request("POST", "/contacts/lists", body={"name": name, "folderId": folder_id})
        return f"Liste {name} créée (ID: {data.get('id', 'N/A')})."
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_get_list_contacts")
async def brevo_get_list_contacts(list_id: int, limit: int = 20, offset: int = 0) -> str:
    """Contacts d'une liste spécifique."""
    try:
        data = await _api_request("GET", f"/contacts/lists/{list_id}/contacts", params={"limit": limit, "offset": offset})
        contacts = data.get("contacts", [])
        total = data.get("count", 0)
        lines = [f"# Contacts liste {list_id} ({len(contacts)}/{total})\n"]
        for c in contacts:
            lines.append(_format_contact_md(c))
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_list_folders")
async def brevo_list_folders(limit: int = 20, offset: int = 0) -> str:
    """Liste les dossiers de contacts."""
    try:
        data = await _api_request("GET", "/contacts/folders", params={"limit": limit, "offset": offset})
        folders = data.get("folders", [])
        lines = [f"# Dossiers ({len(folders)})\n"]
        for f in folders:
            lines.append(f"- **{f.get('name', 'Sans nom')}** (ID: {f.get('id')}) — {f.get('totalSubscribers', 0)} contacts")
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)

# ──────────────────────────────────────────────
# CAMPAGNES EMAIL
# ──────────────────────────────────────────────

@mcp.tool(name="brevo_list_campaigns")
async def brevo_list_campaigns(status: str = "", limit: int = 20, offset: int = 0) -> str:
    """Liste les campagnes email. status: 'draft','sent','queued','archive' (optionnel)."""
    try:
        query: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            query["status"] = status
        data = await _api_request("GET", "/emailCampaigns", params=query)
        campaigns = data.get("campaigns", [])
        total = data.get("count", 0)
        lines = [f"# Campagnes ({len(campaigns)}/{total})\n"]
        for c in campaigns:
            lines.append(_format_campaign_md(c))
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_get_campaign")
async def brevo_get_campaign(campaign_id: int) -> str:
    """Détails complets d'une campagne email."""
    try:
        data = await _api_request("GET", f"/emailCampaigns/{campaign_id}")
        return _format_campaign_md(data)
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_create_campaign")
async def brevo_create_campaign(name: str, subject: str, sender_name: str, sender_email: str, html_content: str, list_ids: str, scheduled_at: str = "") -> str:
    """Crée une campagne email. list_ids: IDs séparés par virgules. scheduled_at: ISO 8601 (optionnel, sinon brouillon)."""
    try:
        ids = [int(x.strip()) for x in list_ids.split(",") if x.strip()]
        body: Dict[str, Any] = {
            "name": name, "subject": subject,
            "sender": {"name": sender_name, "email": sender_email},
            "htmlContent": html_content, "recipients": {"listIds": ids},
        }
        if scheduled_at:
            body["scheduledAt"] = scheduled_at
        data = await _api_request("POST", "/emailCampaigns", body=body)
        return f"Campagne créée. Nom: {name}, ID: {data.get('id', 'N/A')}, Statut: {'programmée' if scheduled_at else 'brouillon'}"
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_send_campaign")
async def brevo_send_campaign(campaign_id: int) -> str:
    """Envoie une campagne immédiatement (irréversible)."""
    try:
        await _api_request("POST", f"/emailCampaigns/{campaign_id}/sendNow")
        return f"Campagne {campaign_id} envoyée !"
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_send_test_campaign")
async def brevo_send_test_campaign(campaign_id: int, email_to: str) -> str:
    """Envoie un test de campagne. email_to: emails séparés par virgules."""
    try:
        emails = [x.strip() for x in email_to.split(",") if x.strip()]
        await _api_request("POST", f"/emailCampaigns/{campaign_id}/sendTest", body={"emailTo": emails})
        return f"Test envoyé à : {', '.join(emails)}"
    except Exception as e:
        return _handle_api_error(e)

# ──────────────────────────────────────────────
# STATISTIQUES
# ──────────────────────────────────────────────

@mcp.tool(name="brevo_get_campaign_stats")
async def brevo_get_campaign_stats(campaign_id: int) -> str:
    """Statistiques détaillées d'une campagne : ouvertures, clics, taux."""
    try:
        data = await _api_request("GET", f"/emailCampaigns/{campaign_id}")
        name = data.get("name", "Sans nom")
        subject = data.get("subject", "N/A")
        stats = data.get("statistics", {}).get("globalStats", {})
        sent = stats.get("sent", 0)
        delivered = stats.get("delivered", 0)
        unique_views = stats.get("uniqueViews", 0)
        unique_clicks = stats.get("uniqueClicks", 0)
        unsubscriptions = stats.get("unsubscriptions", 0)
        hard_bounces = stats.get("hardBounces", 0)
        soft_bounces = stats.get("softBounces", 0)
        spam = stats.get("complaints", 0)
        dr = (delivered / sent * 100) if sent > 0 else 0
        opr = (unique_views / delivered * 100) if delivered > 0 else 0
        cr = (unique_clicks / delivered * 100) if delivered > 0 else 0
        ur = (unsubscriptions / delivered * 100) if delivered > 0 else 0
        return (
            f"# Stats: {name}\nObjet: {subject}\n\n"
            f"Envoyés: {sent} | Délivrés: {delivered} ({dr:.1f}%)\n"
            f"Hard bounces: {hard_bounces} | Soft bounces: {soft_bounces}\n\n"
            f"Ouvertures uniques: {unique_views} ({opr:.1f}%)\n"
            f"Clics uniques: {unique_clicks} ({cr:.1f}%)\n\n"
            f"Désabonnements: {unsubscriptions} ({ur:.1f}%) | Spam: {spam}"
        )
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_get_account")
async def brevo_get_account() -> str:
    """Informations du compte Brevo : plan, crédits."""
    try:
        data = await _api_request("GET", "/account")
        plan = data.get("plan", [{}])
        plan_info = plan[0] if plan else {}
        return (
            f"# Compte Brevo\n\n"
            f"- Entreprise : {data.get('companyName', 'N/A')}\n"
            f"- Email : {data.get('email', 'N/A')}\n"
            f"- Plan : {plan_info.get('type', 'N/A')}\n"
            f"- Crédits : {plan_info.get('credits', 'N/A')}"
        )
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_get_transactional_stats")
async def brevo_get_transactional_stats(days: int = 7, tag: str = "") -> str:
    """Stats emails transactionnels. days: 1-90, tag: optionnel."""
    try:
        query: Dict[str, Any] = {"days": days}
        if tag:
            query["tag"] = tag
        data = await _api_request("GET", "/smtp/statistics/aggregatedReport", params=query)
        return (
            f"# Stats transactionnels ({days} jours)\n\n"
            f"- Requêtes : {data.get('requests', 0)}\n- Délivrés : {data.get('delivered', 0)}\n"
            f"- Ouvertures : {data.get('opens', 0)}\n- Clics : {data.get('clicks', 0)}\n"
            f"- Hard bounces : {data.get('hardBounces', 0)}\n- Soft bounces : {data.get('softBounces', 0)}\n"
            f"- Spam : {data.get('spamReports', 0)}"
        )
    except Exception as e:
        return _handle_api_error(e)

# ──────────────────────────────────────────────
# EMAILS TRANSACTIONNELS
# ──────────────────────────────────────────────

@mcp.tool(name="brevo_send_transactional")
async def brevo_send_transactional(to_email: str, sender_email: str, sender_name: str, subject: str, html_content: str, to_name: str = "", tags: str = "") -> str:
    """Envoie un email transactionnel. tags: séparés par virgules (optionnel)."""
    try:
        to_entry: Dict[str, str] = {"email": to_email}
        if to_name:
            to_entry["name"] = to_name
        body: Dict[str, Any] = {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [to_entry], "subject": subject, "htmlContent": html_content,
        }
        if tags:
            body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        data = await _api_request("POST", "/smtp/email", body=body)
        return f"Email envoyé à {to_email}. Message ID: {data.get('messageId', 'N/A')}"
    except Exception as e:
        return _handle_api_error(e)

@mcp.tool(name="brevo_send_template")
async def brevo_send_template(template_id: int, to_email: str, to_name: str = "", params_json: str = "") -> str:
    """Envoie un email depuis un template Brevo. params_json: JSON des variables dynamiques (optionnel)."""
    try:
        to_entry: Dict[str, str] = {"email": to_email}
        if to_name:
            to_entry["name"] = to_name
        body: Dict[str, Any] = {"templateId": template_id, "to": [to_entry]}
        if params_json:
            body["params"] = json.loads(params_json)
        data = await _api_request("POST", "/smtp/email", body=body)
        return f"Template #{template_id} envoyé à {to_email}. Message ID: {data.get('messageId', 'N/A')}"
    except Exception as e:
        return _handle_api_error(e)

# ──────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="streamable-http")
