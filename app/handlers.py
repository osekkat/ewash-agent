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

# Locale-independent French weekday names. Railway's Linux container defaults
# to the C locale, so strftime("%A") would yield English ("Wednesday" etc.).
_JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _jour_fr(d: date) -> str:
    """Return the capitalized French weekday name for a date (e.g. 'Mercredi')."""
    return _JOURS_FR[d.weekday()].capitalize()


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

    # Moto lane skips model/color questions — straight to location, then service.
    if sess.booking.category == "MOTO":
        await _ask_where(phone, sess)
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
    # Ask location BEFORE showing the service menu — so customers commit to
    # home vs stand before seeing prices (and so the prompt can reflect lieu).
    await _ask_where(phone, sess)


async def _ask_where(phone, sess):
    """Send the home-vs-stand location prompt. Used from both the car lane
    (after color) and the moto lane (after vehicle category)."""
    sess.state = "BOOK_WHERE"
    await meta.send_buttons(
        phone,
        "Où souhaitez-vous le lavage ?\n\n"
        "🚗 *Service à domicile* — Casablanca, sur RDV\n"
        "📍 *Stand physique* — Mall Triangle Vert, Bouskoura | 7j/7 · 09h-22h30",
        [("where_home",   "🚗 À domicile"),
         ("where_center", "📍 Au stand")],
    )


async def _ask_service(phone, sess):
    """Show the car-wash or moto service list, depending on category.

    Called AFTER location (and, for cars, after the promo-code opt-in) — so we
    can hint the chosen lieu (and promo) in the prompt header and avoid
    surprising the customer later in the flow.
    """
    sess.state = "BOOK_SERVICE"
    cat = sess.booking.category
    where_tag = ""
    if sess.booking.location_mode == "home":
        where_tag = " · 🚗 à domicile"
    elif sess.booking.location_mode == "center":
        where_tag = " · 📍 au stand"

    if cat == "MOTO":
        await meta.send_list(
            phone,
            f"Quel type de lavage ?{where_tag}",
            button_label="Voir les tarifs",
            rows=catalog.build_moto_service_rows(),
            section_title="Tarifs moto",
        )
        return

    # Car lane — Lavages catalog (Esthétique is post-confirmation upsell).
    # Promo code (if any) swaps in the partner-preferential prices.
    sess.booking.service_bucket = "wash"
    promo = sess.booking.promo_code or None
    promo_tag = f" · 🎁 {sess.booking.promo_label}" if promo else ""
    section_title = f"🎁 {sess.booking.promo_code}" if promo else f"Lavages · cat. {cat}"
    await meta.send_list(
        phone,
        f"🧼 *Nos formules de lavage*\n_(tarifs pour catégorie {cat}{where_tag}{promo_tag})_",
        button_label="Voir les tarifs",
        rows=catalog.build_car_service_rows(cat, bucket="wash", promo_code=promo),
        section_title=section_title[:24],
    )


async def _handle_book_service(phone, sess, payload_id=None, **kw):
    cat = sess.booking.category
    promo = sess.booking.promo_code or None
    # Valid service IDs depend on the lane:
    #   - moto → SERVICES_MOTO
    #   - car  → SERVICES_WASH (bucket is pre-set to "wash" in _ask_service;
    #           Esthétique is handled separately via the post-confirmation upsell).
    if cat == "MOTO":
        valid = {sid for sid, *_ in catalog.SERVICES_MOTO}
        rows = catalog.build_moto_service_rows()
        section = "Tarifs moto"
        body = "Choisissez un service :"
    else:
        bucket = sess.booking.service_bucket or "all"
        promo_tag = f" · 🎁 {sess.booking.promo_label}" if promo else ""
        if bucket == "wash":
            valid = {sid for sid, *_ in catalog.SERVICES_WASH}
            section = f"🎁 {promo}" if promo else f"Lavages · cat. {cat}"
            body = f"🧼 *Nos formules de lavage*\n_(cat. {cat}{promo_tag})_"
        elif bucket == "detailing":
            valid = {sid for sid, *_ in catalog.SERVICES_DETAILING}
            section = f"🎁 {promo} · Esth." if promo else f"Esthétique · cat. {cat}"
            body = f"✨ *Nos offres d'esthétique*\n_(cat. {cat}{promo_tag})_"
        else:
            valid = {sid for sid, *_ in catalog.SERVICES_CAR}
            section = f"Tarifs catégorie {cat}"
            body = f"Choisissez un service :\n_(cat. {cat}{promo_tag})_"
        section = section[:24]
        rows = catalog.build_car_service_rows(cat, bucket=bucket, promo_code=promo)

    if payload_id not in valid:
        await meta.send_list(phone, body, "Voir les tarifs", rows, section)
        return

    price = catalog.service_price(payload_id, cat, promo_code=promo)
    regular = catalog.service_price(payload_id, cat)  # always the public grid
    name = catalog.service_name(payload_id)
    sess.booking.service = payload_id
    sess.booking.service_label = f"{name} — {price} DH"
    sess.booking.price_dh = price or 0
    sess.booking.price_regular_dh = regular or 0
    # Location is already captured — go straight to date/slot selection.
    await _ask_when(phone, sess)


async def _handle_book_where(phone, sess, payload_id=None, **kw):
    if payload_id == "where_center":
        sess.booking.location_mode = "center"
        if len(catalog.CENTERS) == 1:
            # Only one center → auto-pick and skip selection.
            row = catalog.CENTERS[0]
            sess.booking.center = f"{row[1]} — {row[2]}"
            await _after_location(phone, sess)
        else:
            sess.state = "BOOK_CENTER"
            await meta.send_list(phone, "Quel centre Ewash ?", "Choisir le centre",
                                 catalog.CENTERS, "Centres disponibles")
        return
    if payload_id == "where_home":
        sess.booking.location_mode = "home"
        sess.state = "BOOK_GEO"
        await meta.send_text(
            phone,
            "📍 *Partagez votre localisation*\n\n"
            "Appuyez sur *+* → *Position* (ou *Location*) puis "
            "*Envoyer ma position actuelle*, ou épinglez un lieu sur la carte.",
        )
        return
    await meta.send_buttons(
        phone,
        "Choisissez un lieu :\n\n"
        "🚗 *Service à domicile* — Casablanca, sur RDV\n"
        "📍 *Stand physique* — Mall Triangle Vert, Bouskoura | 7j/7 · 09h-22h30",
        [("where_home",   "🚗 À domicile"),
         ("where_center", "📍 Au stand")],
    )


async def _handle_book_center(phone, sess, payload_id=None, **kw):
    if payload_id not in {row[0] for row in catalog.CENTERS}:
        await meta.send_list(phone, "Choisissez un centre :", "Choisir le centre",
                             catalog.CENTERS, "Centres disponibles")
        return
    sess.booking.center = catalog.label_for(catalog.CENTERS, payload_id)
    await _after_location(phone, sess)


async def _handle_book_geo(phone, sess, location=None, **kw):
    if not location:
        await meta.send_text(
            phone,
            "Je n'ai pas reçu de position 📍\n\n"
            "Appuyez sur *+* → *Position* puis *Envoyer ma position actuelle*, "
            "ou épinglez un lieu sur la carte.",
        )
        return
    parts = []
    if location.get("name"):
        parts.append(location["name"])
    if location.get("address"):
        parts.append(location["address"])
    parts.append(f"📍 {location.get('latitude')}, {location.get('longitude')}")
    sess.booking.geo = " | ".join(parts)
    sess.state = "BOOK_ADDRESS"
    await meta.send_text(
        phone,
        "Merci 🙏\n\nIndiquez maintenant votre *adresse* et toute information utile "
        "pour vous trouver (nom d'immeuble/villa, place de parking, repères…).",
    )


async def _handle_book_address(phone, sess, text=None, **kw):
    if not text or len(text.strip()) < 5:
        await meta.send_text(
            phone,
            "Pouvez-vous me donner plus de détails en texte ? "
            "Adresse précise + infos d'accès (immeuble, étage, code, repères…).",
        )
        return
    sess.booking.address = text.strip()[:300]
    await _after_location(phone, sess)


async def _after_location(phone, sess):
    """Branch after location is fully captured.

    Cars → promo-code opt-in (BOOK_PROMO_ASK).
    Moto → straight to service menu (flyers show no moto discount, and the
    moto catalog is just 2 rows, so keeping the flow short matters).
    """
    if sess.booking.category == "MOTO":
        await _ask_service(phone, sess)
        return
    await _ask_promo(phone, sess)


async def _ask_promo(phone, sess):
    """Ask whether the customer has a partner promo code (cars only).

    A "Yes" routes to BOOK_PROMO_CODE for free-text entry; "No" goes straight
    to the service menu with the public grid. The buttons are intentionally
    action-first so customers without a code don't feel gated.
    """
    sess.state = "BOOK_PROMO_ASK"
    await meta.send_buttons(
        phone,
        "🎁 *Avez-vous un code promo partenaire ?*\n\n"
        "Certains partenaires (résidences, entreprises…) offrent des "
        "tarifs préférentiels à leurs clients.",
        [("promo_no",  "Non, continuer"),
         ("promo_yes", "🎁 J'ai un code")],
    )


async def _handle_book_promo_ask(phone, sess, payload_id=None, **kw):
    if payload_id == "promo_no":
        await _ask_service(phone, sess)
        return
    if payload_id == "promo_yes":
        sess.state = "BOOK_PROMO_CODE"
        await meta.send_text(
            phone,
            "🎁 Écrivez votre *code promo* (ex: *YASMINE*).\n\n"
            "_Tapez *passer* si vous n'en avez pas finalement._",
        )
        return
    # Unknown payload — re-prompt
    await _ask_promo(phone, sess)


async def _handle_book_promo_code(phone, sess, text=None, **kw):
    # Escape hatch: customer changes their mind after tapping "J'ai un code".
    if text and text.strip().lower() in {"passer", "skip", "non", "aucun"}:
        await _ask_service(phone, sess)
        return
    code = catalog.normalize_promo_code(text or "")
    if not code:
        await meta.send_buttons(
            phone,
            "❌ Ce code ne nous dit rien.\n\n"
            "Vérifiez l'orthographe et réessayez, ou continuez sans code.",
            [("promo_no",  "Continuer sans code"),
             ("promo_yes", "🔁 Réessayer le code")],
        )
        sess.state = "BOOK_PROMO_ASK"
        return
    sess.booking.promo_code = code
    sess.booking.promo_label = catalog.promo_label(code)
    await meta.send_text(
        phone,
        f"✅ Code *{code}* appliqué — tarifs *{sess.booking.promo_label}*.",
    )
    await _ask_service(phone, sess)


async def _ask_when(phone, sess, page: int = 0):
    """Show a paginated list of the next 15 open days.

    Page 0 → 8 dates + 'Voir plus' row   (total 9 rows)
    Page 1 → remaining open dates + 'Retour' row (max 8 rows)

    Closed days (Eids, see `catalog.CLOSED_DATES`) are skipped. We scan up to
    25 calendar days forward to find 15 *open* ones — enough cushion for two
    closed days in any reasonable window.
    """
    sess.state = "BOOK_WHEN"
    sess.booking.when_page = page
    open_dates = []
    scan = 0
    while len(open_dates) < 15 and scan < 25:
        d = date.today() + timedelta(days=scan)
        if d.isoformat() not in catalog.CLOSED_DATES:
            open_dates.append(d)
        scan += 1
    sess.booking.when_dates = [d.isoformat() for d in open_dates]

    per_page = 8
    start = page * per_page
    chunk = open_dates[start:start + per_page]
    rows = []
    for i, d in enumerate(chunk):
        idx = start + i
        if page == 0 and i == 0:
            title, desc = "Aujourd'hui", d.strftime("%d/%m/%Y")
        elif page == 0 and i == 1:
            title, desc = "Demain", d.strftime("%d/%m/%Y")
        else:
            title = f"{_jour_fr(d)} {d.strftime('%d/%m')}"
            desc = ""
        rows.append((f"when_d{idx}", title[:24], desc[:72]))

    more = len(open_dates) > start + per_page
    if page == 0 and more:
        rows.append(("when_more", "→ Voir plus de dates", "7 jours supplémentaires"))
    if page > 0:
        rows.append(("when_back", "← Retour", "Revenir aux premières dates"))

    header = "Quel jour ?" if page == 0 else "Plus de dates :"
    section = "Dates disponibles" if page == 0 else "Suite des dates"
    await meta.send_list(phone, header, "Choisir la date", rows, section)


async def _handle_book_when(phone, sess, payload_id=None, **kw):
    # Pagination: "Voir plus" / "Retour" — re-render without advancing state.
    if payload_id == "when_more":
        await _ask_when(phone, sess, page=1)
        return
    if payload_id == "when_back":
        await _ask_when(phone, sess, page=0)
        return

    # Real date pick — payload format: when_d{index} into sess.booking.when_dates
    if payload_id and payload_id.startswith("when_d"):
        try:
            idx = int(payload_id[len("when_d"):])
        except ValueError:
            await _ask_when(phone, sess, page=sess.booking.when_page)
            return
        stored = sess.booking.when_dates or []
        if idx < 0 or idx >= len(stored):
            await _ask_when(phone, sess, page=sess.booking.when_page)
            return
        d = date.fromisoformat(stored[idx])
        today = date.today()
        if d == today:
            label = "Aujourd'hui"
        elif d == today + timedelta(days=1):
            label = "Demain"
        else:
            label = f"{_jour_fr(d)} {d.strftime('%d/%m/%Y')}"
        sess.booking.date_label = label
        sess.state = "BOOK_SLOT"
        await meta.send_list(phone, "À quelle heure ?", "Choisir un créneau",
                             catalog.SLOTS, "Créneaux")
        return

    # Unknown payload — re-render current page
    await _ask_when(phone, sess, page=sess.booking.when_page)


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
    if b.location_mode == "center":
        where_block = f"📍 *Lieu* : 🏢 {b.center}\n"
    else:
        where_block = f"📍 *Lieu* : 🏠 {b.address}\n"
        if b.geo:
            where_block += f"🗺️ *Géoloc.* : {b.geo}\n"
    # Moto lane skips model/color — render vehicle line accordingly.
    if b.category == "MOTO":
        vehicle_line = f"🏍️ *Véhicule* : {b.vehicle_type}\n"
    else:
        vehicle_line = f"🚗 *Véhicule* : {b.vehicle_type} — {b.car_model} ({b.color})\n"
    # Promo tag + savings math (cars only — moto is never discounted).
    promo_line = ""
    if b.promo_code:
        savings = max(0, (b.price_regular_dh or 0) - (b.price_dh or 0))
        if savings > 0:
            promo_line = (
                f"🎁 *Promo* : {b.promo_label} "
                f"(économie -{savings} DH vs tarif public)\n"
            )
        else:
            promo_line = f"🎁 *Promo* : {b.promo_label}\n"
    recap = (
        "📋 *Récapitulatif*\n\n"
        f"👤 *Nom* : {b.name}\n"
        + vehicle_line +
        f"🧼 *Service* : {b.service_label or b.service}\n"
        + promo_line
        + where_block +
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
        # Moto customers have no Esthétique catalog — skip the upsell and end here.
        if sess.booking.category == "MOTO":
            state.reset(phone)
            return
        sess.state = "UPSELL_DETAILING"
        await meta.send_buttons(
            phone,
            "🎁 *Offre du jour*\n\nAjoutez une prestation d'*Esthétique* à votre "
            "rendez-vous et profitez de *-10%* — aujourd'hui seulement.",
            [("upsell_yes", "✨ Voir l'offre"),
             ("upsell_no",  "Non merci")],
        )
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


def _build_detailing_upsell_rows(
    category: str,
    promo_code: str | None = None,
) -> list[tuple[str, str, str]]:
    """Render SERVICES_DETAILING as WhatsApp list rows with prices already
    discounted by 10% (rounded to nearest DH).

    When `promo_code` is set, the 10% is applied on top of the partner price
    (Yasmine customers stack their preferential tariff with the 10% upsell).
    Services not covered by the partner grid fall back to the public price.

    A trailing "Aucune merci" row lets the customer decline the upsell
    without ghosting the conversation.
    """
    rows = []
    for sid, name, desc, prices in catalog.SERVICES_DETAILING:
        base = catalog.service_price(sid, category, promo_code=promo_code)
        if base is None:
            continue
        disc = round(base * 0.9)
        title = f"{name} — {disc} DH"
        rows.append((sid, title[:24], desc[:72]))
    # Escape hatch: always offered as the last row
    rows.append(("upsell_none", "❌ Aucune, merci", "Passer l'offre cette fois"))
    return rows


async def _handle_upsell_detailing(phone, sess, payload_id=None, **kw):
    if payload_id == "upsell_yes":
        cat = sess.booking.category
        promo = sess.booking.promo_code or None
        promo_tag = f" · 🎁 {sess.booking.promo_label}" if promo else ""
        section = f"🎁 {promo} · -10%" if promo else f"Esthétique -10% · {cat}"
        sess.state = "UPSELL_DETAILING_PICK"
        await meta.send_list(
            phone,
            f"✨ *Esthétique à -10%*\n_(remise déjà appliquée, cat. {cat}{promo_tag})_",
            button_label="Choisir prestation",
            rows=_build_detailing_upsell_rows(cat, promo_code=promo),
            section_title=section[:24],
        )
        return
    if payload_id == "upsell_no":
        await meta.send_text(phone, "Parfait, à très vite chez Ewash ! 🙂")
        state.reset(phone)
        return
    # Unknown → re-prompt
    await meta.send_buttons(
        phone,
        "Souhaitez-vous ajouter l'Esthétique à -10% ?",
        [("upsell_yes", "✨ Voir l'offre"), ("upsell_no", "Non merci")],
    )


async def _handle_upsell_detailing_pick(phone, sess, payload_id=None, **kw):
    cat = sess.booking.category
    promo = sess.booking.promo_code or None
    # Escape hatch: user picked "Aucune, merci" — thank & end politely
    if payload_id == "upsell_none":
        await meta.send_text(phone, "Pas de souci, à très vite chez Ewash ! 🙂")
        state.reset(phone)
        return
    valid = {sid for sid, *_ in catalog.SERVICES_DETAILING}
    if payload_id not in valid:
        promo_tag = f" · 🎁 {sess.booking.promo_label}" if promo else ""
        section = f"🎁 {promo} · -10%" if promo else f"Esthétique -10% · {cat}"
        await meta.send_list(
            phone,
            f"✨ *Esthétique à -10%*\n_(remise déjà appliquée, cat. {cat}{promo_tag})_",
            "Choisir la prestation",
            _build_detailing_upsell_rows(cat, promo_code=promo),
            section[:24],
        )
        return
    base = catalog.service_price(payload_id, cat, promo_code=promo)
    disc = round(base * 0.9) if base is not None else 0
    name = catalog.service_name(payload_id)
    label = f"{name} — {disc} DH (-10%)"
    sess.booking.addon_service = payload_id
    sess.booking.addon_service_label = label
    sess.booking.addon_price_dh = disc
    from .booking import update_booking
    update_booking(
        sess.booking.ref,
        addon_service=payload_id,
        addon_service_label=label,
        addon_price_dh=disc,
    )
    main = sess.booking.service_label or sess.booking.service or "—"
    total = (sess.booking.price_dh or 0) + disc
    await meta.send_text(
        phone,
        f"✅ *Add-on enregistré !*\n\n"
        f"Votre réservation *{sess.booking.ref}* a bien été mise à jour :\n\n"
        f"🧼 *Lavage* : {main}\n"
        f"✨ *Esthétique (-10%)* : {label}\n"
        f"💰 *Total indicatif* : {total} DH\n\n"
        f"_Le tarif reste indicatif — l'équipe confirme selon l'état du véhicule._\n\n"
        f"L'équipe Ewash confirmera lors de l'intervention. À très vite ! 🙏",
    )
    state.reset(phone)


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
    "IDLE":                  _handle_idle,
    "MENU":                  _handle_menu,
    "HANDOFF":               _handle_handoff,
    "BOOK_NAME":             _handle_book_name,
    "BOOK_VEHICLE":          _handle_book_vehicle,
    "BOOK_MODEL":            _handle_book_model,
    "BOOK_COLOR":            _handle_book_color,
    "BOOK_SERVICE":          _handle_book_service,
    "BOOK_WHERE":            _handle_book_where,
    "BOOK_CENTER":           _handle_book_center,
    "BOOK_GEO":              _handle_book_geo,
    "BOOK_ADDRESS":          _handle_book_address,
    "BOOK_PROMO_ASK":        _handle_book_promo_ask,
    "BOOK_PROMO_CODE":       _handle_book_promo_code,
    "BOOK_WHEN":             _handle_book_when,
    "BOOK_SLOT":             _handle_book_slot,
    "BOOK_NOTE":             _handle_book_note,
    "BOOK_NOTE_TEXT":        _handle_book_note_text,
    "BOOK_CONFIRM":          _handle_book_confirm,
    "UPSELL_DETAILING":      _handle_upsell_detailing,
    "UPSELL_DETAILING_PICK": _handle_upsell_detailing_pick,
}
