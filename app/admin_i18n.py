"""French-first admin UI translations.

The admin portal is for Omar/Ewash staff, so every label defaults to French.
English remains available for Oussama/developer support through an explicit
language switch.
"""
from __future__ import annotations

DEFAULT_LOCALE = "fr"
SUPPORTED_LOCALES = ("fr", "en")

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "fr": {
        "nav.dashboard": "Tableau de bord",
        "nav.bookings": "Réservations",
        "nav.customers": "Clients",
        "nav.prices": "Prix",
        "nav.promos": "Promos",
        "nav.reminders": "Rappels",
        "nav.closed_dates": "Fermetures",
        "nav.time_slots": "Créneaux",
        "nav.centers": "Centres",
        "nav.copy": "Textes",
        "nav.logout": "Déconnexion",
        "action.save": "Enregistrer",
        "action.cancel": "Annuler",
        "action.edit": "Modifier",
        "action.delete": "Supprimer",
        "action.language_fr": "FR",
        "action.language_en": "EN",
        "admin.not_configured.title": "Portail admin non configuré",
        "admin.not_configured.body": "Ajoutez ADMIN_PASSWORD pour activer le portail.",
        "status.draft": "Brouillon",
        "status.awaiting_confirmation": "En attente de confirmation",
        "status.confirmed": "Confirmée",
        "status.rescheduled": "Reportée",
        "status.customer_cancelled": "Annulée par le client",
        "status.admin_cancelled": "Annulée par l'équipe",
        "status.expired": "Expirée",
        "status.no_show": "Client absent",
        "status.technician_en_route": "Technicien en route",
        "status.arrived": "Arrivé",
        "status.in_progress": "Nettoyage en cours",
        "status.completed": "Voiture nettoyée",
        "status.completed_with_issue": "Terminée avec incident",
        "status.refunded": "Remboursée",
    },
    "en": {
        "nav.dashboard": "Dashboard",
        "nav.bookings": "Bookings",
        "nav.customers": "Customers",
        "nav.prices": "Prices",
        "nav.promos": "Promos",
        "nav.reminders": "Reminders",
        "nav.closed_dates": "Closures",
        "nav.time_slots": "Time slots",
        "nav.centers": "Centers",
        "nav.copy": "Copy",
        "nav.logout": "Logout",
        "action.save": "Save",
        "action.cancel": "Cancel",
        "action.edit": "Edit",
        "action.delete": "Delete",
        "action.language_fr": "FR",
        "action.language_en": "EN",
        "admin.not_configured.title": "Admin portal is not configured",
        "admin.not_configured.body": "Set ADMIN_PASSWORD to enable the portal.",
        "status.draft": "Draft",
        "status.awaiting_confirmation": "Awaiting confirmation",
        "status.confirmed": "Confirmed",
        "status.rescheduled": "Rescheduled",
        "status.customer_cancelled": "Cancelled by customer",
        "status.admin_cancelled": "Cancelled by team",
        "status.expired": "Expired",
        "status.no_show": "No-show",
        "status.technician_en_route": "Technician en route",
        "status.arrived": "Arrived",
        "status.in_progress": "In progress",
        "status.completed": "Completed",
        "status.completed_with_issue": "Completed with issue",
        "status.refunded": "Refunded",
    },
}

_NAV_KEYS = (
    "nav.dashboard",
    "nav.bookings",
    "nav.customers",
    "nav.prices",
    "nav.promos",
    "nav.reminders",
    "nav.closed_dates",
    "nav.time_slots",
    "nav.centers",
    "nav.copy",
)


def normalize_locale(locale: str | None) -> str:
    """Return a supported locale, defaulting to French."""
    if not locale:
        return DEFAULT_LOCALE
    normalized = locale.strip().lower()
    return normalized if normalized in SUPPORTED_LOCALES else DEFAULT_LOCALE


def t(key: str, locale: str | None = None) -> str:
    """Translate an admin UI key.

    Missing English keys fall back to French. Missing French keys return the key
    itself, which keeps templates visible instead of crashing.
    """
    lang = normalize_locale(locale)
    if key in _TRANSLATIONS[lang]:
        return _TRANSLATIONS[lang][key]
    return _TRANSLATIONS[DEFAULT_LOCALE].get(key, key)


def admin_nav_labels(locale: str | None = None) -> list[str]:
    """Localized admin navigation labels, French by default."""
    return [t(key, locale) for key in _NAV_KEYS]
