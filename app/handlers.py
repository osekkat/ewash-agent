"""Inbound WhatsApp dispatcher — button/list-driven booking flow.

Entry: handle_message(message, contact) is called for each inbound message.
We advance the per-phone state machine and send the next prompt.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import catalog, meta, state
from .booking import Booking

log = logging.getLogger(__name__)


# ── Top-level entry ────────────────────────────────────────────────────────
async def handle_message(message: dict, contact: dict | None = None) -> None:
    phone = message.get("from")
    if not phone:
        return

    payload_id = meta.extract_interactive_id(message)
    text = meta.extract_text(message)
    location = meta.extract_location(message)

    sess = state.get(phone)
    log.info("inbound phone=%s state=%s payload=%s text=%r",
             phone, sess.state, payload_id, text)

    # Global escape hatches (work in any state)
    if text and text.strip().lower() in {"reset", "annuler", "cancel", "/reset"}:
        state.reset(phone)
        await _send_menu(phone, greeting="Conversation réinitialisée.")
        return
    if text and text.strip().lower() in {"menu", "start", "bonjour", "salam", "hi", "hello"}:
        state.reset(phone)
        await _send_menu(phone)
        return

    # Dispatch on state
    handler = _DISPATCH.get(sess.state, _handle_idle)
    await handler(phone, sess, payload_id=payload_id, text=text, location=location,
                  contact=contact)


# ── Individual state handlers ──────────────────────────────────────────────
async def _handle_idle(phone, sess, **kw):
    # Anything in IDLE → show the welcome menu.
    await _send_menu(phone)


async def _handle_menu(phone, sess, payload_id=None, text=None, **kw):
    if payload_id == "menu_book":
        state.start_booking(phone)
        await meta.send_text(phone, "Parfait ! 📝\n\nComment vous appelez-vous ?")
        return
    if payload_id == "menu_services":
        await _show_services_info(phone)
        await _send_menu(phone, greeting="Autre chose ?")
        return
    if payload_id == "menu_human":
        sess.state = "HANDOFF"
        await meta.send_text(
            phone,
            "👋 Écrivez votre message ci-dessous et un membre de l'équipe Ewash "
            "vous recontactera très rapidement.",
        )
        return
    # Unknown → re-prompt
    await _send_menu(phone, greeting="Je n'ai pas compris. Choisissez une option :")


async def _handle_handoff(phone, sess, text=None, **kw):
    # Log a handoff request — in v0.3 we'll notify Omar on his WhatsApp.
    if text:
        log.warning("HANDOFF REQUEST phone=%s text=%r", phone, text)
        await meta.send_text(
            phone,
            "✅ Merci ! Votre message a bien été transmis à l'équipe Ewash. "
            "Nous vous recontacterons dès que possible.",
        )
        state.reset(phone)
    else:
        await meta.send_text(phone, "Merci d'écrire votre message en texte.")


async def _handle_book_name(phone, sess, text=None, **kw):
    if not text or len(text.strip()) < 2:
        await meta.send_text(phone, "Pouvez-vous me donner votre nom ?")
        return
    sess.booking.name = text.strip()[:60]
    sess.state = "BOOK_VEHICLE"
    await meta.send_list(
        phone,
        f"Merci {sess.booking.name} 👋\n\nQuel type de véhicule ?",
        button_label="Choisir le véhicule",
        rows=catalog.VEHICLE_CATEGORIES,
        section_title="Catégories",
    )


async def _handle_book_vehicle(phone, sess, payload_id=None, **kw):
    if payload_id not in catalog.VEHICLE_CATEGORY_KEY:
        await meta.send_list(
            phone, "Choisissez le type de véhicule :",
            "Choisir le véhicule",
            catalog.VEHICLE_CATEGORIES, "Catégories",
        )
        return
    sess.booking.vehicle_type = catalog.label_for(catalog.VEHICLE_CATEGORIES, payload_id)
    sess.booking.category = catalog.VEHICLE_CATEGORY_KEY[payload_id]

    # Moto lane skips model/color questions — straight to service list.
    if sess.booking.category == "MOTO":
        sess.state = "BOOK_SERVICE"
        await meta.send_list(
            phone,
            "Quel type de lavage ?",
            button_label="Voir les tarifs",
            rows=catalog.build_moto_service_rows(),
            section_title="Tarifs moto",
        )
        return

    sess.state = "BOOK_MODEL"
    await meta.send_text(
        phone,
        "Quelle est la marque et le modèle ? (ex: *Dacia Logan*, *Toyota RAV4*)",
    )


async def _handle_book_model(phone, sess, text=None, **kw):
    if not text or len(text.strip()) < 2:
        await meta.send_text(phone, "Indiquez la marque et le modèle, svp.")
        return
    sess.booking.car_model = text.strip()[:60]
    sess.state = "BOOK_COLOR"
    await meta.send_text(
        phone,
        "Quelle est la *couleur* du véhicule ? (ex: *Blanc*, *Gris métallisé*, *Rouge bordeaux*)",
    )


async def _handle_book_color(phone, sess, payload_id=None, text=None, **kw):
    if text and text.strip():
        sess.booking.color = text.strip()[:30]
    else:
        await meta.send_text(
            phone,
            "Merci d'indiquer la couleur du véhicule (ex: *Blanc*, *Gris*, *Bleu nuit*).",
        )
        return
    sess.state = "BOOK_SERVICE"
    # Show services with prices inline for the customer's category.
    rows = catalog.build_car_service_rows(sess.booking.category)
    cat_letter = sess.booking.category  # A / B / C
    await meta.send_list(
        phone,
        f"Quel service souhaitez-vous ?\n_(tarifs pour catégorie {cat_letter})_",
        button_label="Voir les tarifs",
        rows=rows,
        section_title=f"Tarifs catégorie {cat_letter}",
    )


async def _handle_book_service(phone, sess, payload_id=None, **kw):
    cat = sess.booking.category
    # Valid service IDs depend on whether we're in the moto lane or car lane.
    if cat == "MOTO":
        valid = {sid for sid, *_ in catalog.SERVICES_MOTO}
        rows = catalog.build_moto_service_rows()
        section = "Tarifs moto"
    else:
        valid = {sid for sid, *_ in catalog.SERVICES_CAR}
        rows = catalog.build_car_service_rows(cat)
        section = f"Tarifs catégorie {cat}"

    if payload_id not in valid:
        await meta.send_list(phone, "Choisissez un service :", "Voir les tarifs",
                             rows, section)
        return

    price = catalog.service_price(payload_id, cat)
    name = catalog.service_name(payload_id)
    sess.booking.service = payload_id
    sess.booking.service_label = f"{name} — {price} DH"
    sess.booking.price_dh = price or 0
    sess.state = "BOOK_WHERE"
    await meta.send_buttons(
        phone,
        "Où souhaitez-vous le lavage ?",
        [("where_center", "🏢 Centre Ewash"), ("where_home", "🏠 À domicile")],
    )


async def _handle_book_where(phone, sess, payload_id=None, **kw):
    if payload_id == "where_center":
        sess.booking.location_mode = "center"
        if len(catalog.CENTERS) == 1:
            # Only one center → auto-pick and skip selection.
            sess.booking.center = catalog.CENTERS[0][1]
            await _ask_when(phone, sess)
        else:
            sess.state = "BOOK_CENTER"
            await meta.send_list(phone, "Quel centre Ewash ?", "Choisir le centre",
                                 catalog.CENTERS, "Centres disponibles")
        return
    if payload_id == "where_home":
        sess.booking.location_mode = "home"
        sess.state = "BOOK_ADDRESS"
        await meta.send_text(
            phone,
            "📍 Partagez votre *adresse* :\n\n"
            "• Envoyez-la en texte\n"
            "• Ou utilisez 📎 → Position pour envoyer une épingle",
        )
        return
    await meta.send_buttons(phone, "Choisissez un lieu :",
                            [("where_center", "🏢 Centre Ewash"),
                             ("where_home", "🏠 À domicile")])


async def _handle_book_center(phone, sess, payload_id=None, **kw):
    if payload_id not in {row[0] for row in catalog.CENTERS}:
        await meta.send_list(phone, "Choisissez un centre :", "Choisir le centre",
                             catalog.CENTERS, "Centres disponibles")
        return
    sess.booking.center = catalog.label_for(catalog.CENTERS, payload_id)
    await _ask_when(phone, sess)


async def _handle_book_address(phone, sess, text=None, location=None, **kw):
    if location:
        parts = []
        if location.get("name"):
            parts.append(location["name"])
        if location.get("address"):
            parts.append(location["address"])
        parts.append(f"📍 {location.get('latitude')}, {location.get('longitude')}")
        sess.booking.address = " | ".join(parts)
    elif text and len(text.strip()) >= 5:
        sess.booking.address = text.strip()[:200]
    else:
        await meta.send_text(
            phone,
            "Pouvez-vous envoyer une adresse plus précise ? (texte ou 📍 position)",
        )
        return
    await _ask_when(phone, sess)


async def _ask_when(phone, sess):
    sess.state = "BOOK_WHEN"
    today = date.today()
    rows = [
        ("when_today",    "Aujourd'hui",   today.strftime("%d/%m/%Y")),
        ("when_tomorrow", "Demain",        (today + timedelta(days=1)).strftime("%d/%m/%Y")),
        ("when_plus2",    f"{(today + timedelta(days=2)).strftime('%a %d/%m')}", ""),
        ("when_plus3",    f"{(today + timedelta(days=3)).strftime('%a %d/%m')}", ""),
        ("when_plus4",    f"{(today + timedelta(days=4)).strftime('%a %d/%m')}", ""),
        ("when_plus5",    f"{(today + timedelta(days=5)).strftime('%a %d/%m')}", ""),
    ]
    await meta.send_list(phone, "Quel jour ?", "Choisir la date", rows, "Dates disponibles")


async def _handle_book_when(phone, sess, payload_id=None, **kw):
    mapping = {
        "when_today":    ("Aujourd'hui",  0),
        "when_tomorrow": ("Demain",        1),
        "when_plus2":    ("",              2),
        "when_plus3":    ("",              3),
        "when_plus4":    ("",              4),
        "when_plus5":    ("",              5),
    }
    if payload_id not in mapping:
        await _ask_when(phone, sess)
        return
    label, delta = mapping[payload_id]
    d = date.today() + timedelta(days=delta)
    sess.booking.date_label = label or d.strftime("%A %d/%m/%Y")
    if not label:  # for +2..+5 use the actual date as label
        sess.booking.date_label = d.strftime("%A %d/%m/%Y")
    sess.state = "BOOK_SLOT"
    await meta.send_list(phone, "À quelle heure ?", "Choisir un créneau",
                         catalog.SLOTS, "Créneaux")


async def _handle_book_slot(phone, sess, payload_id=None, **kw):
    if payload_id not in {row[0] for row in catalog.SLOTS}:
        await meta.send_list(phone, "Choisissez un créneau :", "Choisir un créneau",
                             catalog.SLOTS, "Créneaux")
        return
    sess.booking.slot = catalog.label_for(catalog.SLOTS, payload_id)
    sess.state = "BOOK_NOTE"
    await meta.send_buttons(
        phone,
        "Souhaitez-vous ajouter une note (tâches particulières, instructions d'accès…) ?",
        [("note_skip", "Non, passer"), ("note_add", "Ajouter une note")],
    )


async def _handle_book_note(phone, sess, payload_id=None, **kw):
    if payload_id == "note_skip":
        await _send_recap(phone, sess)
        return
    if payload_id == "note_add":
        sess.state = "BOOK_NOTE_TEXT"
        await meta.send_text(phone, "✍️ Écrivez votre note :")
        return
    await meta.send_buttons(
        phone, "Souhaitez-vous ajouter une note ?",
        [("note_skip", "Non, passer"), ("note_add", "Ajouter une note")],
    )


async def _handle_book_note_text(phone, sess, text=None, **kw):
    if not text:
        await meta.send_text(phone, "Merci d'écrire votre note en texte.")
        return
    sess.booking.note = text.strip()[:300]
    await _send_recap(phone, sess)


async def _send_recap(phone, sess):
    b = sess.booking
    where = (f"🏢 {b.center}" if b.location_mode == "center"
             else f"🏠 {b.address}")
    # Moto lane skips model/color — render vehicle line accordingly.
    if b.category == "MOTO":
        vehicle_line = f"🏍️ *Véhicule* : {b.vehicle_type}\n"
    else:
        vehicle_line = f"🚗 *Véhicule* : {b.vehicle_type} — {b.car_model} ({b.color})\n"
    recap = (
        "📋 *Récapitulatif*\n\n"
        f"👤 *Nom* : {b.name}\n"
        + vehicle_line +
        f"🧼 *Service* : {b.service_label or b.service}\n"
        f"📍 *Lieu* : {where}\n"
        f"🗓️ *Date* : {b.date_label}\n"
        f"⏰ *Créneau* : {b.slot}\n"
        f"📞 *Téléphone* : +{b.phone}\n"
    )
    if b.note:
        recap += f"📝 *Note* : {b.note}\n"
    recap += (
        "\n_Le tarif affiché est indicatif — l'équipe confirme selon l'état "
        "du véhicule._\n\nTout est correct ?"
    )
    sess.state = "BOOK_CONFIRM"
    await meta.send_buttons(
        phone, recap,
        [("confirm_yes", "✅ Confirmer"),
         ("confirm_edit", "✏️ Modifier"),
         ("confirm_no",  "❌ Annuler")],
    )


async def _handle_book_confirm(phone, sess, payload_id=None, **kw):
    if payload_id == "confirm_yes":
        ref = sess.booking.assign_ref()
        await meta.send_text(
            phone,
            f"✅ *Réservation enregistrée !*\n\n"
            f"Référence : *{ref}*\n\n"
            f"L'équipe Ewash vous contactera très prochainement pour confirmer "
            f"le créneau et le tarif. Merci de votre confiance ! 🙏",
        )
        state.reset(phone)
        return
    if payload_id == "confirm_edit":
        # Simple approach: restart the flow, keeping the phone as key.
        state.start_booking(phone)
        await meta.send_text(phone,
            "Reprenons 🙂\n\nComment vous appelez-vous ?")
        return
    if payload_id == "confirm_no":
        state.reset(phone)
        await meta.send_text(phone,
            "Réservation annulée. Envoyez *menu* pour recommencer à tout moment.")
        return
    # Unknown payload — re-show recap
    await _send_recap(phone, sess)


# ── Helpers ─────────────────────────────────────────────────────────────────
async def _send_menu(phone: str, greeting: str | None = None) -> None:
    state.reset(phone)
    sess = state.get(phone)
    sess.state = "MENU"
    body = (greeting + "\n\n" if greeting else
            "👋 *Bienvenue chez Ewash* — lavage auto écologique sans eau.\n\n")
    body += "Que souhaitez-vous faire ?"
    await meta.send_buttons(
        phone, body,
        [("menu_book",     "📅 Prendre RDV"),
         ("menu_services", "🧼 Nos services"),
         ("menu_human",    "💬 Parler à l'équipe")],
    )


async def _show_services_info(phone: str) -> None:
    lines = ["*🧼 Nos services Ewash* _(tarifs A/B/C en DH)_:\n"]
    for _id, name, desc, prices in catalog.SERVICES_CAR:
        price_str = f"{prices['A']}/{prices['B']}/{prices['C']} DH"
        lines.append(f"• *{name}* — {price_str}\n  _{desc}_")
    lines.append("")
    lines.append("*🏍️ Moto* :")
    for _id, name, desc, price in catalog.SERVICES_MOTO:
        lines.append(f"• *{name}* — {price} DH  _{desc}_")
    lines.append("")
    lines.append("*Catégories de véhicule* :")
    lines.append("A = Citadine · B = Berline/SUV moyen · C = Grande berline/SUV")
    await meta.send_text(phone, "\n".join(lines))


# ── Dispatch table ─────────────────────────────────────────────────────────
_DISPATCH = {
    "IDLE":            _handle_idle,
    "MENU":            _handle_menu,
    "HANDOFF":         _handle_handoff,
    "BOOK_NAME":       _handle_book_name,
    "BOOK_VEHICLE":    _handle_book_vehicle,
    "BOOK_MODEL":      _handle_book_model,
    "BOOK_COLOR":      _handle_book_color,
    "BOOK_SERVICE":    _handle_book_service,
    "BOOK_WHERE":      _handle_book_where,
    "BOOK_CENTER":     _handle_book_center,
    "BOOK_ADDRESS":    _handle_book_address,
    "BOOK_WHEN":       _handle_book_when,
    "BOOK_SLOT":       _handle_book_slot,
    "BOOK_NOTE":       _handle_book_note,
    "BOOK_NOTE_TEXT":  _handle_book_note_text,
    "BOOK_CONFIRM":    _handle_book_confirm,
}
