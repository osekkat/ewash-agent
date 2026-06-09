from app.admin_i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    admin_nav_labels,
    normalize_locale,
    t,
)


def test_admin_language_defaults_to_french():
    assert DEFAULT_LOCALE == "fr"
    assert SUPPORTED_LOCALES == ("fr", "en")
    assert normalize_locale(None) == "fr"
    assert normalize_locale("") == "fr"
    assert normalize_locale("es") == "fr"


def test_admin_labels_are_french_by_default_with_english_option():
    assert t("nav.bookings") == "Réservations"
    assert t("nav.reminders") == "Rappels"
    assert t("action.save") == "Enregistrer"

    assert t("nav.bookings", "en") == "Bookings"
    assert t("nav.reminders", "en") == "Reminders"
    assert t("action.save", "en") == "Save"


def test_admin_navigation_is_localized():
    assert admin_nav_labels() == [
        "Tableau de bord",
        "Réservations",
        "Clients",
        "Paie agents",
        "Suivi opérationnel",
        "Effacements",
        "Prix",
        "Promos",
        "Rappels",
        "Notifications",
        "Fermetures",
        "Créneaux",
        "Centres",
        "Textes",
    ]
    assert admin_nav_labels("en")[:6] == [
        "Dashboard",
        "Bookings",
        "Customers",
        "Agent payroll",
        "Operational tracking",
        "Erasures",
    ]
