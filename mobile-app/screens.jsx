/* eslint-disable */
// ewash — main tabs: Home, Bookings, Services, Profile + Support

const { useState: useS_h, useEffect: useE_h, useMemo: useM_h, useRef: useR_h } = React;

// Smoothly count from 0 to `target` over `duration` ms (ease-out cubic).
// Respects prefers-reduced-motion.
function useCountUp(target, duration = 1200, enabled = true) {
  const [value, setValue] = useS_h(0);
  useE_h(() => {
    if (!enabled) { setValue(target); return; }
    if (typeof window === 'undefined') { setValue(target); return; }
    const reduced = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) { setValue(target); return; }
    let raf, start;
    const tick = (ts) => {
      if (start === undefined) start = ts;
      const t = Math.min(1, (ts - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(target * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration, enabled]);
  return value;
}

function _positiveNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

const EWASH_IMPACT_LITERS_PER_WASH = 300;
const EWASH_IMPACT_DRINKING_MONTHS_PER_WASH = 6;
const EWASH_DETAILING_SERVICE_IDS = new Set(['svc_pol', 'svc_cer6m', 'svc_cer6w', 'svc_cuir', 'svc_plastq', 'svc_optq', 'svc_lustre']);
const EWASH_COMPLETED_WASH_STATUSES = new Set(['completed', 'completed_with_issue']);
const EWASH_PROFILE_VEHICLES_KEY = 'ewash.profile_vehicles';
const EWASH_PROFILE_ADDRESSES_KEY = 'ewash.profile_addresses';
const EWASH_NOTIFICATIONS_KEY = 'ewash.notifications_enabled';

function _officialWhatsAppPhone() {
  return (typeof window !== 'undefined' && window.EWASH_OFFICIAL_WHATSAPP) || ('+212' + '611204502');
}

function _readLocalArray(key) {
  try {
    if (typeof localStorage === 'undefined') return [];
    const parsed = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return [];
  }
}

function _writeLocalArray(key, rows) {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(key, JSON.stringify(Array.isArray(rows) ? rows : []));
    }
  } catch (_) {
    if (window.EwashLog) window.EwashLog.warn('localstorage.error', { op: 'set', key });
  }
}

function _profileStorageKeys() {
  return {
    name: (window.EwashAPI && window.EwashAPI._NAME_KEY) || 'ewash.name',
    phone: (window.EwashAPI && window.EwashAPI._PHONE_KEY) || 'ewash.phone',
  };
}

function _normalizeProfilePhone(value) {
  let digits = String(value || '').replace(/\D/g, '');
  if (digits.startsWith('212')) digits = digits.slice(3);
  if (digits.startsWith('0')) digits = digits.slice(1);
  return digits.slice(0, 9);
}

function _vehicleDisplayLabel(vehicle) {
  if (!vehicle) return '';
  return [vehicle.make, vehicle.color].filter(Boolean).join(' · ') || vehicle.category || '';
}

function _addressDisplayLabel(address) {
  if (!address) return '';
  return address.label || address.address || address.details || '';
}

function _profileRowId(prefix) {
  return prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(16).slice(2, 6);
}

function _mergeVehicleRows(localRows, apiRows) {
  const seen = new Set();
  return (localRows || []).concat(apiRows || []).filter((row) => {
    const key = [row.category, row.make, row.color, row.plate].join('|').toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function _readImpactStats() {
  const source = typeof window !== 'undefined' ? window.EWASH_IMPACT_STATS : null;
  const washCount = _positiveNumber(source && source.wash_count) || 0;
  const litersSaved = _positiveNumber(source && source.liters_saved) || (washCount * EWASH_IMPACT_LITERS_PER_WASH);
  return { litersSaved, washCount };
}

function _isCompletedWashBooking(booking) {
  if (!booking || !EWASH_COMPLETED_WASH_STATUSES.has(booking.status)) return false;
  const serviceId = booking.service_id || '';
  return !EWASH_DETAILING_SERVICE_IDS.has(serviceId);
}

function _impactStatsFromBookings(bookings) {
  const washCount = (bookings || []).filter(_isCompletedWashBooking).length;
  return {
    washCount,
    litersSaved: washCount * EWASH_IMPACT_LITERS_PER_WASH,
  };
}

function _drinkingWaterLabel(t, washCount) {
  const months = washCount * EWASH_IMPACT_DRINKING_MONTHS_PER_WASH;
  if (months <= 0) return t.impactDrinkingEmpty || "0 mois d'eau à boire";
  if (months < 12) {
    return (t.impactDrinkingMonths || "{months} mois d'eau à boire").replace('{months}', months);
  }
  const years = Math.floor(months / 12);
  const rem = months % 12;
  const yearLabel = years + ' ' + (years > 1 ? (t.years || 'ans') : (t.year || 'an'));
  if (!rem) return (t.impactDrinkingYears || "{years} d'eau à boire").replace('{years}', yearLabel);
  return (t.impactDrinkingYearsMonths || "{years} et {months} mois d'eau à boire")
    .replace('{years}', yearLabel)
    .replace('{months}', rem);
}

// ─────────────────────────────────────────────────────────────
// HOME
// ─────────────────────────────────────────────────────────────
function HomeScreen({ t, lang, openBooking, gotoSupport, gotoBookings, gotoTariffs, theme, variant, profile, staffContact }) {
  const [impactStats, setImpactStats] = useS_h(_readImpactStats);
  useE_h(() => {
    if (!window.EwashAPI || !window.EwashAPI.getMyBookings) return undefined;
    let alive = true;
    window.EwashAPI.getMyBookings()
      .then((resp) => {
        if (!alive) return;
        setImpactStats(_impactStatsFromBookings((resp && resp.bookings) || []));
      })
      .catch((err) => {
        if (window.EwashLog && err && err.error_code !== 'no_local_token') {
          window.EwashLog.warn('home.impact.fetch_failed', { error_code: err.error_code || 'fetch_failed' });
        }
      });
    return function () { alive = false; };
  }, []);
  const litersCount = useCountUp(impactStats.litersSaved || 0, 1400, true);
  const washCount = useCountUp(impactStats.washCount || 0, 900, true);
  const drinkingLabel = _drinkingWaterLabel(t, impactStats.washCount || 0);
  const openNotifications = () => {
    if (window.EwashLog) window.EwashLog.info('home.notifications.opened', {});
    if (gotoBookings) gotoBookings();
  };
  return (
    <div className="app-scroll">
      <div className="appbar">
        <div className="row gap-10">
          <img src="assets/ewash_logo_only.png" width={32} height={32} alt="Ewash"
            style={{ display: 'block', flexShrink: 0 }}/>
          {profile.name ? (
            <div className="col" style={{ gap: 2 }}>
              <div className="t-tiny" style={{ color: 'var(--text-2)', fontWeight: 600 }}>
                {t.welcome},
              </div>
              <div style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 15, lineHeight: 1 }}>
                {profile.name}
              </div>
            </div>
          ) : (
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 17, lineHeight: 1 }}>
              {t.welcome} 👋
            </div>
          )}
        </div>
        <div className="row gap-4">
          <HelpButton t={t} staffContact={staffContact} currentScreen="home" />
          <button className="icon-btn" type="button" aria-label={t.notifications || 'Notifications'} onClick={openNotifications} title={t.notifications || 'Notifications'}>
            <div style={{ position: 'relative' }}>
              <Icons.Bell size={22} />
              <span style={{
                position: 'absolute', top: -2, right: -2,
                width: 9, height: 9, borderRadius: 99,
                background: 'var(--accent)', border: '2px solid var(--bg)',
                boxShadow: '0 0 0 0 color-mix(in srgb, var(--accent) 70%, transparent)',
                animation: 'dotPulse 2.4s ease-out infinite',
              }}/>
            </div>
          </button>
        </div>
      </div>

      <div className="px-16 col gap-20 anim-stagger" style={{ paddingBottom: 24 }}>
        {/* HERO */}
        <div className="hero">
          <div style={{
            fontFamily: 'var(--font-display)', fontWeight: 800,
            fontSize: 30, lineHeight: 1.05, color: '#fff',
            margin: '0 auto 18px', position: 'relative', zIndex: 1,
            letterSpacing: '-0.02em', maxWidth: 280,
            textAlign: 'center', whiteSpace: 'pre-line',
          }}>
            {lang === 'ar' ? 'Ewash\nغسيل سيارات\nبدون ماء' : 'Ewash\nLavage auto\nSans eau'}
          </div>
          <button onClick={openBooking}
            className="press hero-cta-bounce"
            style={{
              background: variant === 'premium' ? 'var(--gold)' : '#fff',
              color: variant === 'premium' ? '#0a0a0a' : 'var(--primary)',
              border: 'none', borderRadius: 999,
              padding: '14px 24px', fontWeight: 700, fontSize: 15,
              letterSpacing: '-0.01em',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              width: 'fit-content', margin: '0 auto',
              position: 'relative', zIndex: 1, cursor: 'pointer',
              boxShadow: '0 8px 22px -8px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.6)',
            }}>
            {t.bookCta}
            <Icons.ChevronRight size={18} stroke={2.5} />
          </button>
        </div>

        {/* YOUR IMPACT */}
        <div className="card card-elev" style={{
          padding: '18px 18px 16px', borderRadius: 22,
          textAlign: 'center', maxWidth: 360, width: '100%', margin: '0 auto',
        }}>
          <div className="row center gap-6 mb-8">
            <Icons.Drop size={17} style={{ color: 'var(--primary)' }} />
            <span className="t-tiny" style={{ color: 'var(--text-2)', fontWeight: 800, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
              {t.yourImpact || 'Votre Impact'}
            </span>
          </div>
          <div className="t-num" style={{ fontWeight: 850, fontSize: 34, color: 'var(--text)', lineHeight: 1 }}>
            {litersCount.toLocaleString('fr-FR')}
            <span style={{ fontSize: 16, color: 'var(--text-2)', marginInlineStart: 4 }}>L</span>
          </div>
          <div className="t-muted" style={{ marginTop: 6, fontWeight: 650 }}>
            {t.waterSaved || "Litres d'eau économisés"}
          </div>
          <div className="row center gap-8 mt-12 wrap">
            <span className="chip chip-primary">{washCount} {t.washMetric || 'lavages'}</span>
            <span className="chip chip-accent">{drinkingLabel}</span>
          </div>
          <div className="t-tiny" style={{ color: 'var(--text-3)', marginTop: 10 }}>
            {t.impactFormula || "300 L économisés par lavage · 300 L = 6 mois d'eau à boire pour 1 personne"}
          </div>
        </div>

        {/* NEXT APPOINTMENT */}
        <HomeNextAppointmentSection
          t={t}
          openBooking={openBooking}
          gotoSupport={gotoSupport}
          staffContact={staffContact}
        />

        {/* QUICK ACTIONS */}
        <div>
          <div className="t-h3 mb-12">{t.quickActions}</div>
          <div className="row gap-10">
            <button onClick={gotoTariffs} className="card press" style={{
              flex: 1, padding: 14, borderRadius: 18, textAlign: 'inherit',
              display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-start',
              cursor: 'pointer',
            }}>
              <div style={{
                width: 38, height: 38, borderRadius: 12,
                background: 'var(--accent-soft)', color: 'var(--accent-soft-text)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.5)',
              }}><Icons.Tag size={18}/></div>
              <div style={{ fontWeight: 700, fontSize: 13.5, letterSpacing: '-0.005em' }}>{t.viewTariffs}</div>
            </button>
            <button onClick={() => _openTeamChat(t, staffContact)} className="card press" style={{
              flex: 1, padding: 14, borderRadius: 18, textAlign: 'inherit',
              display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-start',
              cursor: 'pointer',
            }}>
              <div style={{
                width: 38, height: 38, borderRadius: 12,
                background: 'var(--primary-soft)', color: 'var(--primary-soft-text)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.5)',
              }}><Icons.Message size={18}/></div>
              <div style={{ fontWeight: 700, fontSize: 13.5, letterSpacing: '-0.005em' }}>{t.talkTeam}</div>
            </button>
          </div>
        </div>

        {/* PROMISE */}
        <div className="card card-soft" style={{
          background: 'var(--surface-2)', padding: 16, borderRadius: 20,
        }}>
          <div className="row gap-10 mb-8">
            <div style={{
              width: 32, height: 32, borderRadius: 10,
              background: variant === 'premium' ? 'var(--accent)' : 'var(--accent)',
              color: '#0a1a0a',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}><Icons.Shield size={17}/></div>
            <div style={{ fontWeight: 700, fontSize: 14 }}>{t.ourPromise}</div>
          </div>
          <div className="t-muted">{t.promiseBody}</div>
        </div>
      </div>
    </div>
  );
}

const HOME_FINAL_STATUSES = new Set([
  'customer_cancelled',
  'admin_cancelled',
  'expired',
  'no_show',
  'completed',
  'completed_with_issue',
  'refunded',
]);

function _todayIsoLocal() {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return y + '-' + m + '-' + d;
}

function _bookingSortValue(booking) {
  const dateIso = (booking && booking.date_iso) || '';
  const hour = Number(booking && booking.slot_start_hour);
  const normalizedHour = Number.isFinite(hour) ? hour : 99;
  return dateIso + 'T' + String(normalizedHour).padStart(2, '0');
}

function _nextFutureBooking(bookings) {
  const today = _todayIsoLocal();
  return (bookings || [])
    .filter(function (booking) {
      if (!booking || !booking.date_iso) return false;
      if (booking.date_iso < today) return false;
      if (HOME_FINAL_STATUSES.has(booking.status)) return false;
      return true;
    })
    .slice()
    .sort(function (a, b) {
      return _bookingSortValue(a).localeCompare(_bookingSortValue(b));
    })[0] || null;
}

function _homeDateBadge(booking) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec((booking && booking.date_iso) || '');
  if (!match) return { weekday: 'RDV', day: '—', month: '' };
  const y = Number(match[1]);
  const m = Number(match[2]);
  const d = Number(match[3]);
  const dateObj = new Date(y, m - 1, d);
  const weekdays = ['DIM', 'LUN', 'MAR', 'MER', 'JEU', 'VEN', 'SAM'];
  return {
    weekday: weekdays[dateObj.getDay()],
    day: String(d).padStart(2, '0'),
    month: (_FR_MONTH_ABBREV[m - 1] || '').toUpperCase(),
  };
}

function _homeBookingTitle(booking) {
  const service = booking.service_label || booking.service_id || 'Réservation Ewash';
  const vehicle = booking.vehicle_label || '';
  return vehicle ? service + ' · ' + vehicle : service;
}

function _openBookingHelp(booking, staffContact, fallback, intent) {
  if (window.EwashLog) {
    window.EwashLog.info('home.next_appointment.' + intent, { ref: booking && booking.ref });
  }
  const phone = _officialWhatsAppPhone();
  const action = intent === 'edit' ? 'modifier' : 'suivre';
  const text = "Bonjour, je souhaite " + action + " ma réservation Ewash " + ((booking && booking.ref) || '') + ".";
  const url = _waLinkFor(phone, text);
  if (url) {
    window.open(url, '_blank');
    return;
  }
  if (fallback) fallback();
}

// Home "Parler à l'équipe" tile. Always opens the official Ewash WhatsApp Business number.
function _openTeamChat(t, _staffContact) {
  if (window.EwashLog) window.EwashLog.info('home.talk_team.opened', { phone: 'official' });
  const phone = _officialWhatsAppPhone();
  const text = (t && t.talkTeamMessage) ||
    "Bonjour Ewash, je souhaite discuter avec votre équipe.";
  const url = _waLinkFor(phone, text);
  if (url) window.open(url, '_blank', 'noopener,noreferrer');
}

function HomeNextAppointmentSection({ t, openBooking, gotoSupport, staffContact }) {
  const [uiState, setUiState] = useS_h('loading');
  const [booking, setBooking] = useS_h(null);

  useE_h(() => {
    let alive = true;
    setUiState('loading');
    setBooking(null);

    if (typeof navigator !== 'undefined' && navigator && navigator.onLine === false) {
      setUiState('empty');
      return function () { alive = false; };
    }

    if (!window.EwashAPI || !window.EwashAPI.getMyBookings) {
      setUiState('error');
      return function () { alive = false; };
    }

    window.EwashAPI.getMyBookings()
      .then(function (resp) {
        if (!alive) return;
        const items = (resp && resp.bookings) || [];
        const next = _nextFutureBooking(items);
        setBooking(next);
        setUiState(next ? 'ready' : 'empty');
        if (window.EwashLog) {
          window.EwashLog.info('home.next_appointment.loaded', {
            count: items.length,
            has_next: !!next,
            ref: next && next.ref,
          });
        }
      })
      .catch(function (err) {
        if (!alive) return;
        const error_code = (err && err.error_code) || null;
        const status = (err && err.status) || null;
        if (error_code === 'invalid_token') {
          try { localStorage.removeItem('ewash.bookings_token'); } catch (e) { /* ignore */ }
        }
        if (window.EwashLog) {
          window.EwashLog.warn('home.next_appointment.error', { error_code: error_code, status: status });
        }
        setBooking(null);
        setUiState(error_code === 'no_local_token' || error_code === 'invalid_token' ? 'empty' : 'error');
      });

    return function () { alive = false; };
  }, []);

  return (
    <div>
      <div className="row between" style={{ marginBottom: 10 }}>
        <div className="t-h3">{t.nextAppointment}</div>
        {booking && (
          <span className="chip chip-accent">
            <span className="live-dot" />
            {t.upcoming}
          </span>
        )}
      </div>

      {uiState === 'loading' && <HomeAppointmentSkeleton />}
      {uiState === 'ready' && booking && (
        <HomeAppointmentCard
          t={t}
          booking={booking}
          staffContact={staffContact}
          gotoSupport={gotoSupport}
        />
      )}
      {(uiState === 'empty' || uiState === 'error') && (
        <HomeNoAppointmentCard
          t={t}
          onBook={openBooking}
          isError={uiState === 'error'}
        />
      )}
    </div>
  );
}

function HomeAppointmentSkeleton() {
  return (
    <div className="card card-elev" style={{ padding: 16, opacity: 0.65 }}>
      <div className="row gap-12">
        <div style={{
          width: 56, minWidth: 56, height: 64,
          borderRadius: 14,
          background: 'var(--surface-2)',
        }} />
        <div className="col gap-8 flex-1">
          <div style={{ height: 14, width: '65%', borderRadius: 6, background: 'var(--surface-2)' }} />
          <div style={{ height: 11, width: '46%', borderRadius: 6, background: 'var(--surface-2)' }} />
          <div style={{ height: 11, width: '58%', borderRadius: 6, background: 'var(--surface-2)' }} />
        </div>
      </div>
    </div>
  );
}

function HomeNoAppointmentCard({ t, onBook, isError }) {
  return (
    <div className="card card-elev center" style={{
      padding: 18,
      flexDirection: 'column',
      alignItems: 'flex-start',
      gap: 12,
    }}>
      <div className="row gap-10">
        <div style={{
          width: 38, height: 38, borderRadius: 12,
          background: 'var(--primary-soft)',
          color: 'var(--primary-soft-text)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Icons.Calendar size={18} />
        </div>
        <div className="col gap-3" style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 14.5 }}>
            {isError ? (t.homeUpcomingError || t.networkErrorTitle || 'Connexion impossible') : (t.homeNoUpcoming || 'Aucun rendez-vous à venir')}
          </div>
          <div className="t-muted" style={{ fontSize: 13 }}>
            {isError ? (t.homeUpcomingErrorBody || 'Vos rendez-vous ne peuvent pas être chargés pour le moment.') : (t.homeNoUpcomingBody || 'Réservez votre prochain lavage en quelques étapes.')}
          </div>
        </div>
      </div>
      <Btn variant="soft" onClick={onBook} style={{ width: '100%' }}>
        {t.bookNow || t.bookCta || 'Réserver un lavage'}
      </Btn>
    </div>
  );
}

function HomeAppointmentCard({ t, booking, staffContact, gotoSupport }) {
  const badge = _homeDateBadge(booking);
  const trackBooking = function () {
    _openBookingHelp(booking, staffContact, gotoSupport, 'track');
  };
  const editBooking = function () {
    _openBookingHelp(booking, staffContact, gotoSupport, 'edit');
  };
  return (
    <div className="card card-elev" style={{ padding: 16 }}>
      <div className="row gap-12">
        <div style={{
          width: 56, minWidth: 56,
          borderRadius: 14, padding: '10px 0',
          background: 'var(--primary-soft)',
          color: 'var(--primary-soft-text)',
          textAlign: 'center',
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.5), 0 6px 14px -8px color-mix(in srgb, var(--primary) 35%, transparent)',
        }}>
          <div className="t-tiny" style={{ fontWeight: 700, opacity: 0.85, letterSpacing: '0.12em' }}>{badge.weekday}</div>
          <div className="t-num" style={{ fontWeight: 800, fontSize: 22, lineHeight: 1 }}>{badge.day}</div>
          <div className="t-tiny" style={{ opacity: 0.85, letterSpacing: '0.08em' }}>{badge.month}</div>
        </div>
        <div className="col gap-4 flex-1" style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 15 }}>{_homeBookingTitle(booking)}</div>
          <div className="t-tiny" style={{ color: 'var(--text-3)', fontWeight: 700 }}>{booking.ref}</div>
          <div className="t-muted" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Icons.Clock size={13}/> {booking.slot_label || 'Créneau à confirmer'}
          </div>
          <div className="t-muted" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Icons.Pin size={13}/> {booking.location_label || 'Lieu à confirmer'}
          </div>
        </div>
      </div>
      <div className="row gap-8 mt-12">
        <Btn variant="soft" onClick={trackBooking} style={{ flex: 1 }}>{t.track}</Btn>
        <button
          className="btn btn-secondary"
          onClick={editBooking}
          aria-label={(t.edit || 'Modifier') + ' ' + booking.ref}
          style={{ flex: '0 0 auto' }}
        >
          <Icons.Edit size={16}/>
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// BOOKINGS HISTORY — live data via EwashAPI.getMyBookings()
// ─────────────────────────────────────────────────────────────

const _FR_MONTH_ABBREV = ['jan', 'fév', 'mar', 'avr', 'mai', 'juin', 'juil', 'aoû', 'sep', 'oct', 'nov', 'déc'];

const _INERT_STATUSES = new Set([
  'customer_cancelled',
  'admin_cancelled',
  'expired',
  'no_show',
  'completed',
  'completed_with_issue',
  'refunded',
]);

const _REUSABLE_STATUSES = new Set([
  'completed',
  'completed_with_issue',
  'customer_cancelled',
  'admin_cancelled',
  'expired',
  'no_show',
]);

function _bookingChipClass(status) {
  if (status === 'confirmed' || status === 'rescheduled' ||
      status === 'technician_en_route' || status === 'arrived' ||
      status === 'in_progress') return 'chip chip-accent';
  if (status === 'completed' || status === 'completed_with_issue') return 'chip chip-primary';
  return 'chip';
}

function _isUpcomingBookingForList(booking) {
  if (!booking) return false;
  if (_INERT_STATUSES.has(booking.status)) return false;
  if (!booking.date_iso) return true;
  return booking.date_iso >= _todayIsoLocal();
}

function _sortBookingsAsc(a, b) {
  return _bookingSortValue(a).localeCompare(_bookingSortValue(b));
}

function _sortBookingsDesc(a, b) {
  return _bookingSortValue(b).localeCompare(_bookingSortValue(a));
}

function _waLinkFor(phone, text) {
  const digits = String(phone || '').replace(/[^0-9]/g, '');
  if (!digits) return null;
  return 'https://wa.me/' + digits + '?text=' + encodeURIComponent(text);
}

function BookingsScreen({ t, lang, openBooking, theme, staffContact }) {
  const [uiState, setUiState] = useS_h('loading');
  const [bookings, setBookings] = useS_h([]);
  const [selectedRef, setSelectedRef] = useS_h(null);
  const [fetchTick, setFetchTick] = useS_h(0);

  const refetch = () => setFetchTick(function (n) { return n + 1; });

  useE_h(() => {
    let alive = true;
    setUiState('loading');

    if (typeof navigator !== 'undefined' && navigator && navigator.onLine === false) {
      setUiState('offline');
      return function () { alive = false; };
    }

    if (!window.EwashAPI || !window.EwashAPI.getMyBookings) {
      // api.js failed to load (CDN block, network, etc.). Surface as a
      // generic error rather than silently rendering an empty page.
      setUiState('error');
      return function () { alive = false; };
    }

    window.EwashAPI.getMyBookings()
      .then(function (resp) {
        if (!alive) return;
        const items = (resp && resp.bookings) || [];
        if (window.EwashLog) window.EwashLog.info('bookings.list', { count: items.length });
        if (!items.length) { setBookings([]); setUiState('empty'); return; }
        setBookings(items);
        setUiState('list');
      })
      .catch(function (err) {
        if (!alive) return;
        const error_code = (err && err.error_code) || null;
        const status = (err && err.status) || null;
        if (window.EwashLog) window.EwashLog.warn('bookings.list.error', { error_code: error_code, status: status });
        if (error_code === 'no_local_token') { setUiState('no_token'); return; }
        if (error_code === 'invalid_token') {
          // The token the PWA holds is no longer valid (server-revoked,
          // hand-deleted, or DB wiped). Drop it so the next fresh booking
          // mints a clean replacement.
          try { localStorage.removeItem('ewash.bookings_token'); } catch (e) { /* ignore */ }
          setUiState('no_token');
          return;
        }
        setUiState('error');
      });

    return function () { alive = false; };
  }, [fetchTick]);

  const selected = selectedRef ? bookings.find(function (b) { return b.ref === selectedRef; }) : null;
  const upcomingBookings = bookings
    .filter(_isUpcomingBookingForList)
    .slice()
    .sort(_sortBookingsAsc);
  const pastBookings = bookings
    .filter(function (booking) { return !_isUpcomingBookingForList(booking); })
    .slice()
    .sort(_sortBookingsDesc);

  return (
    <div className="app-scroll">
      <TopBar title={t.bookings} right={null} t={t} staffContact={staffContact} currentScreen="bookings" />
      <div className="px-16 col gap-16 anim-stagger" style={{ paddingBottom: 24 }}>

        {uiState === 'loading' && <BookingsLoadingSkeleton />}

        {uiState === 'offline' && (
          <BookingsErrorCard
            title={t.networkErrorTitle || 'Hors ligne'}
            message="Pas de connexion. Réessayez quand vous revenez en ligne."
            onRetry={refetch}
          />
        )}

        {uiState === 'error' && (
          <BookingsErrorCard
            title={t.networkErrorTitle || 'Erreur'}
            message="Impossible de charger vos réservations."
            onRetry={refetch}
          />
        )}

        {uiState === 'no_token' && (
          <BookingsNoTokenCard t={t} onBook={openBooking} />
        )}

        {uiState === 'empty' && (
          <BookingsEmptyCard t={t} onBook={openBooking} />
        )}

        {uiState === 'list' && (
          <React.Fragment>
            <BookingListSection
              title={t.upcoming || 'À venir'}
              bookings={upcomingBookings}
              emptyText={t.noUpcomingBookings || 'Aucun rendez-vous à venir'}
              onTap={function (b) { setSelectedRef(b.ref); }}
            />
            <BookingListSection
              title={t.past || 'Passés'}
              bookings={pastBookings}
              emptyText={t.noPastBookings || 'Aucun rendez-vous passé'}
              onTap={function (b) { setSelectedRef(b.ref); }}
            />
          </React.Fragment>
        )}
      </div>

      <Sheet open={!!selected} onClose={function () { setSelectedRef(null); }}>
        {selected && (
          <BookingDetailContent
            booking={selected}
            onClose={function () { setSelectedRef(null); }}
            t={t}
            lang={lang}
            staffContact={staffContact}
            openBooking={openBooking}
          />
        )}
      </Sheet>
    </div>
  );
}

function BookingListSection({ title, bookings, emptyText, onTap }) {
  return (
    <section className="col gap-10">
      <div className="row between" style={{ paddingInline: 4 }}>
        <div className="t-h3">{title}</div>
        <span className="chip">{bookings.length}</span>
      </div>
      {bookings.length ? bookings.map(function (b) {
        return (
          <BookingCard
            key={b.ref}
            booking={b}
            onTap={function () { onTap(b); }}
          />
        );
      }) : (
        <div className="card-soft text-center" style={{ padding: 16, borderRadius: 16 }}>
          <div className="t-muted">{emptyText}</div>
        </div>
      )}
    </section>
  );
}

function BookingsLoadingSkeleton() {
  return (
    <React.Fragment>
      {[0, 1, 2].map(function (i) {
        return (
          <div key={i} className="card" style={{ padding: 16, opacity: 0.55 }}>
            <div className="row gap-12">
              <div style={{ width: 50, height: 50, borderRadius: 12, background: 'var(--surface-2)' }} />
              <div className="col gap-8 flex-1">
                <div style={{ height: 14, width: '60%', borderRadius: 6, background: 'var(--surface-2)' }} />
                <div style={{ height: 11, width: '40%', borderRadius: 6, background: 'var(--surface-2)' }} />
                <div style={{ height: 11, width: '50%', borderRadius: 6, background: 'var(--surface-2)' }} />
              </div>
            </div>
          </div>
        );
      })}
    </React.Fragment>
  );
}

function BookingsErrorCard({ title, message, onRetry }) {
  return (
    <div className="card center" style={{
      padding: '36px 24px', flexDirection: 'column', gap: 12,
      background: 'var(--surface-2)', border: 'none',
    }}>
      <div style={{
        width: 56, height: 56, borderRadius: 18,
        background: 'var(--surface)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--text-3)',
      }}>
        <Icons.Bell size={26} />
      </div>
      <div className="col gap-4" style={{ alignItems: 'center', textAlign: 'center' }}>
        <div style={{ fontWeight: 700, fontSize: 14.5 }}>{title}</div>
        <div className="t-muted" style={{ fontSize: 13 }}>{message}</div>
      </div>
      <Btn variant="soft" onClick={onRetry} style={{ marginTop: 4 }}>
        Réessayer
      </Btn>
    </div>
  );
}

function BookingsNoTokenCard({ t, onBook }) {
  return (
    <div className="card center" style={{
      padding: '36px 24px', flexDirection: 'column', gap: 12,
      background: 'var(--surface-2)', border: 'none',
    }}>
      <div style={{
        width: 56, height: 56, borderRadius: 18,
        background: 'var(--surface)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--text-3)',
      }}>
        <Icons.Calendar size={26} />
      </div>
      <div className="col gap-4" style={{ alignItems: 'center', textAlign: 'center' }}>
        <div style={{ fontWeight: 700, fontSize: 14.5 }}>Réservez votre premier lavage</div>
        <div className="t-muted" style={{ fontSize: 13 }}>
          Vos rendez-vous apparaîtront ici dès votre première réservation.
        </div>
      </div>
      <Btn variant="soft" onClick={onBook} style={{ marginTop: 4 }}>
        {t.bookCta || 'Commencer'}
      </Btn>
    </div>
  );
}

function BookingsEmptyCard({ t, onBook }) {
  return (
    <div className="card center" style={{
      padding: '36px 24px', flexDirection: 'column', gap: 12,
      background: 'var(--surface-2)', border: 'none',
    }}>
      <div style={{
        width: 56, height: 56, borderRadius: 18,
        background: 'var(--surface)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--text-3)',
      }}>
        <Icons.Calendar size={26} />
      </div>
      <div className="col gap-4" style={{ alignItems: 'center', textAlign: 'center' }}>
        <div style={{ fontWeight: 700, fontSize: 14.5 }}>Aucune réservation</div>
        <div className="t-muted" style={{ fontSize: 13 }}>Vos rendez-vous apparaîtront ici</div>
      </div>
      <Btn variant="soft" onClick={onBook} style={{ marginTop: 4 }}>
        {t.bookCta || 'Réserver maintenant'}
      </Btn>
    </div>
  );
}

function BookingCard({ booking, onTap }) {
  let dayNum = '–';
  let monthAbbrev = '';
  if (booking.date_iso) {
    const parts = booking.date_iso.split('-');
    if (parts.length === 3) {
      dayNum = parts[2];
      const monthIdx = parseInt(parts[1], 10) - 1;
      if (monthIdx >= 0 && monthIdx < _FR_MONTH_ABBREV.length) {
        monthAbbrev = _FR_MONTH_ABBREV[monthIdx];
      }
    }
  }
  return (
    <button
      className="card"
      style={{
        padding: 0, overflow: 'hidden',
        textAlign: 'left', width: '100%',
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        cursor: 'pointer',
      }}
      onClick={onTap}
    >
      <div className="row gap-12" style={{ padding: 16 }}>
        <div style={{
          width: 50, minWidth: 50,
          borderRadius: 12,
          background: 'var(--surface-2)',
          color: 'var(--text)',
          textAlign: 'center',
          padding: '8px 0',
        }}>
          <div className="t-num" style={{ fontWeight: 800, fontSize: 18, lineHeight: 1.1 }}>{dayNum}</div>
          <div className="t-tiny" style={{ letterSpacing: '0.1em', textTransform: 'uppercase' }}>{monthAbbrev}</div>
        </div>
        <div className="col gap-4 flex-1" style={{ minWidth: 0 }}>
          <div className="row between">
            <div style={{ fontWeight: 700, fontSize: 14.5 }}>{booking.service_label || '—'}</div>
            <span className={_bookingChipClass(booking.status)} style={{ fontSize: 10.5, padding: '3px 8px' }}>
              {booking.status_label || booking.status}
            </span>
          </div>
          <div className="t-muted" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <Icons.Clock size={12} /> {booking.slot_label || '—'}{booking.vehicle_label ? ' · ' + booking.vehicle_label : ''}
          </div>
          <div className="t-muted" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <Icons.Pin size={12} /> {booking.location_label || '—'}
          </div>
        </div>
      </div>
    </button>
  );
}

function BookingDetailContent({ booking, onClose, t, lang, staffContact, openBooking }) {
  const canCalendar = !_INERT_STATUSES.has(booking.status);
  const canRebook = _REUSABLE_STATUSES.has(booking.status);

  const shareWhatsApp = function () {
    const phone = _officialWhatsAppPhone();
    const text = "Bonjour, ma réservation Ewash " + booking.ref + " le " + (booking.date_label || '') + " à " + (booking.slot_label || '') + ". Pouvez-vous me donner plus d'infos ?";
    const url = _waLinkFor(phone, text);
    if (!url) return;
    if (window.EwashLog) window.EwashLog.info('bookings.share', { ref: booking.ref, channel: 'whatsapp', phone: 'official' });
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  const contactSupport = function () {
    const phone = _officialWhatsAppPhone();
    const text = "Bonjour, j'ai besoin d'aide concernant ma réservation " + booking.ref + ".";
    const url = _waLinkFor(phone, text);
    if (!url) return;
    if (window.EwashLog) window.EwashLog.info('bookings.contact_support', { ref: booking.ref, phone: 'official' });
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  const bookAgain = function () {
    if (window.EwashLog) {
      window.EwashLog.info('bookings.detail.book_again', { ref: booking.ref, service_id: booking.service_id });
    }
    onClose();
    if (openBooking) openBooking();
  };

  const addToCalendar = function () {
    if (window.EwashLog) window.EwashLog.info('bookings.detail.calendar', { ref: booking.ref });
    if (window.EwashCalendar && window.EwashCalendar.download) {
      try {
        window.EwashCalendar.download(booking, lang);
        return;
      } catch (err) {
        if (window.EwashLog) {
          window.EwashLog.warn('bookings.detail.calendar_error', {
            ref: booking.ref,
            error_code: (err && err.error_code) || 'calendar_export_failed',
          });
        }
      }
    }
    // Fallback: Google Calendar template URL. Works on every mobile browser
    // and falls back to a friendly "Add event" UI on desktop.
    if (!booking.date_iso) return;
    const date = booking.date_iso.replace(/-/g, '');
    const startH = String(booking.slot_start_hour || 9).padStart(2, '0');
    const endH = String(booking.slot_end_hour || (booking.slot_start_hour || 9) + 2).padStart(2, '0');
    const dates = date + 'T' + startH + '0000/' + date + 'T' + endH + '0000';
    const title = 'Ewash ' + booking.ref + ' — ' + (booking.service_label || '');
    const location = booking.location_label || '';
    const url = 'https://calendar.google.com/calendar/render?action=TEMPLATE&text=' + encodeURIComponent(title) + '&dates=' + dates + '&location=' + encodeURIComponent(location);
    window.open(url, '_blank');
  };

  return (
    <div className="col gap-16" style={{ padding: '8px 16px 24px' }}>
      <div className="col gap-6">
        <div style={{ fontWeight: 700, fontSize: 18 }}>{booking.ref}</div>
        <span className={_bookingChipClass(booking.status)} style={{ fontSize: 11, padding: '3px 8px', alignSelf: 'flex-start' }}>
          {booking.status_label || booking.status}
        </span>
      </div>

      <div className="col" style={{ gap: 0 }}>
        <BookingDetailRow label="Service" value={booking.service_label} />
        <BookingDetailRow label="Véhicule" value={booking.vehicle_label} />
        <BookingDetailRow label="Date" value={booking.date_label} />
        <BookingDetailRow label="Créneau" value={booking.slot_label} />
        <BookingDetailRow label="Lieu" value={booking.location_label} />
        <BookingDetailRow label="Total" value={(booking.total_price_dh || 0) + ' DH'} />
      </div>

      <div className="col gap-8">
        {canCalendar && (
          <Btn variant="soft" onClick={addToCalendar}>
            <Icons.Calendar size={16} />&nbsp;{t.addToCalendar || 'Ajouter au calendrier'}
          </Btn>
        )}
        <Btn variant="soft" onClick={shareWhatsApp}>
          <Icons.Send size={16} />&nbsp;Partager via WhatsApp
        </Btn>
        {canRebook && (
          <Btn variant="soft" onClick={bookAgain}>
            <Icons.Plus size={16} />&nbsp;Réserver à nouveau
          </Btn>
        )}
        {staffContact && staffContact.available && staffContact.whatsapp_phone && (
          <Btn variant="soft" onClick={contactSupport}>
            <Icons.Message size={16} />&nbsp;Contacter le support
          </Btn>
        )}
        <Btn variant="primary" onClick={onClose}>
          Fermer
        </Btn>
      </div>
    </div>
  );
}

function BookingDetailRow({ label, value }) {
  return (
    <div className="row between" style={{ gap: 16, padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
      <div className="t-muted" style={{ fontSize: 13 }}>{label}</div>
      <div style={{ fontSize: 13.5, fontWeight: 600, textAlign: 'right' }}>{value || '—'}</div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// SERVICES / TARIFFS
// ─────────────────────────────────────────────────────────────
const TARIFF_CATEGORIES = ['A', 'B', 'C'];
const TARIFF_BUCKETS = [
  ['lavage', 'wash'],
  ['esthetique', 'detailing'],
];
const SERVICE_DURATION_MINUTES = {
  svc_ext: 30,
  svc_cpl: 60,
  svc_sal: 120,
};
const DETAILING_DISCOUNT_RATE = 0.20;
const DETAILING_SORT_ORDER = {
  svc_pol: 10,
  svc_lustre: 20,
  svc_cer6m: 30,
  svc_cer6w: 40,
};

function _serviceDuration(service) {
  if (!service) return 45;
  return SERVICE_DURATION_MINUTES[service.id] || service.duration_min || service.durationMin || 45;
}

function _durationLabel(t, minutes) {
  if (minutes >= 60 && minutes % 60 === 0) {
    const hours = minutes / 60;
    return (t.fromDuration || 'À partir de {duration}').replace('{duration}', hours + 'h');
  }
  return (t.fromDuration || 'À partir de {duration}').replace('{duration}', minutes + ' ' + (t.min || 'min'));
}

function _displayServiceName(service) {
  if (!service || !service.id) return service && service.name;
  if (service.id === 'svc_cer6m') return 'Céramique 6 mois';
  if (service.id === 'svc_cer6w') return 'Céramique 6 Semaines';
  return service.name;
}

function _displayServiceDesc(service) {
  if (!service || !service.id) return service && service.desc;
  if (service.id === 'svc_cer6m') return 'Protection céramique 6 mois · polissage nécessaire pour en bénéficier';
  return service.desc;
}

function _discountedDetailingPrice(price) {
  return Math.round((price || 0) * (1 - DETAILING_DISCOUNT_RATE));
}
function _mergeTariffCatalog(results) {
  const grouped = {
    lavage: new Map(),
    esthetique: new Map(),
  };
  (results || []).forEach(({ category, body }) => {
    const services = (body && body.services) || {};
    TARIFF_BUCKETS.forEach(([screenBucket, apiBucket]) => {
      (services[apiBucket] || []).forEach((service) => {
        if (!service || !service.id) return;
        let row = grouped[screenBucket].get(service.id);
        if (!row) {
          row = {
            id: service.id,
            name: _displayServiceName(service),
            desc: _displayServiceDesc(service),
            durationMin: _serviceDuration(service),
            prices: {},
          };
          grouped[screenBucket].set(service.id, row);
        }
        row.prices[category] = service.price_dh || 0;
      });
    });
  });
  const detailingRows = Array.from(grouped.esthetique.values()).sort(function (a, b) {
    return (DETAILING_SORT_ORDER[a.id] || 999) - (DETAILING_SORT_ORDER[b.id] || 999);
  });
  return {
    lavage: Array.from(grouped.lavage.values()),
    esthetique: detailingRows,
  };
}

function ServicesScreen({ t, lang, openBooking, theme, staffContact }) {
  const [tab, setTab] = useS_h('lavage');
  const [catalogState, setCatalogState] = useS_h({
    loading: true,
    error: '',
    lavage: [],
    esthetique: [],
  });
  const [reloadTick, setReloadTick] = useS_h(0);
  const [selectedDetailingIds, setSelectedDetailingIds] = useS_h([]);
  const [selectedCategory, setSelectedCategory] = useS_h('A');
  const [tariffVehicles, setTariffVehicles] = useS_h(() => _readLocalArray(EWASH_PROFILE_VEHICLES_KEY));

  useE_h(() => {
    if (!window.EwashAPI || !window.EwashAPI.getMyVehicles) return undefined;
    let alive = true;
    window.EwashAPI.getMyVehicles()
      .then((payload) => {
        if (!alive) return;
        const apiVehicles = (payload && Array.isArray(payload.vehicles)) ? payload.vehicles : [];
        const vehicles = _mergeVehicleRows(_readLocalArray(EWASH_PROFILE_VEHICLES_KEY), apiVehicles);
        setTariffVehicles(vehicles);
        if (vehicles[0] && vehicles[0].category && TARIFF_CATEGORIES.includes(vehicles[0].category)) {
          setSelectedCategory(vehicles[0].category);
        }
      })
      .catch((err) => {
        if (window.EwashLog && err && err.error_code !== 'no_local_token') {
          window.EwashLog.warn('tariffs.vehicles.fetch_failed', { error_code: err.error_code || 'fetch_failed' });
        }
      });
    return function () { alive = false; };
  }, []);

  useE_h(() => {
    let alive = true;
    if (!window.EwashAPI || !window.EwashAPI.getBootstrap) {
      setCatalogState((prev) => Object.assign({}, prev, {
        loading: false,
        error: 'api_unavailable',
      }));
      return function () { alive = false; };
    }

    setCatalogState((prev) => Object.assign({}, prev, { loading: true, error: '' }));
    Promise.all(
      TARIFF_CATEGORIES.map((category) => (
        window.EwashAPI.getBootstrap({ category }).then((body) => ({ category, body }))
      ))
    )
      .then((results) => {
        if (!alive) return;
        const merged = _mergeTariffCatalog(results);
        setCatalogState({
          loading: false,
          error: '',
          lavage: merged.lavage,
          esthetique: merged.esthetique,
        });
        if (window.EwashLog) {
          window.EwashLog.info('tariffs.catalog_loaded', {
            wash_count: merged.lavage.length,
            detailing_count: merged.esthetique.length,
          });
        }
      })
      .catch((err) => {
        if (!alive) return;
        setCatalogState((prev) => Object.assign({}, prev, {
          loading: false,
          error: (err && err.error_code) || 'catalog_failed',
        }));
        if (window.EwashLog) {
          window.EwashLog.warn('tariffs.catalog_error', {
            error_code: (err && err.error_code) || 'catalog_failed',
            status: err && err.status,
          });
        }
      });

    return function () { alive = false; };
  }, [reloadTick]);

  useE_h(() => {
    setSelectedDetailingIds((prev) => prev.filter((id) => catalogState.esthetique.some((item) => item.id === id)));
  }, [catalogState.esthetique]);

  const isDetailingTab = tab === 'esthetique';
  const items = tab === 'lavage' ? catalogState.lavage : catalogState.esthetique;
  const selectedDetailingItems = catalogState.esthetique.filter((item) => selectedDetailingIds.includes(item.id));
  const selectedDetailingTotal = selectedDetailingItems.reduce((sum, item) => sum + _discountedDetailingPrice(item.prices[selectedCategory] || item.prices.A || 0), 0);
  const toggleDetailingService = (id) => {
    setSelectedDetailingIds((prev) => prev.includes(id)
      ? prev.filter((itemId) => itemId !== id)
      : prev.concat(id));
  };
  const startDetailingBooking = () => {
    if (!selectedDetailingIds.length) return;
    const [serviceId, ...addonIds] = selectedDetailingIds;
    openBooking({
      category: selectedCategory,
      serviceId,
      addons: addonIds,
      source: 'services_detailing',
    });
  };
  const startWashBooking = (service) => {
    openBooking({ category: selectedCategory, serviceId: service.id, source: 'services_wash' });
  };
  return (
    <div className="app-scroll services-screen">
      <TopBar title={t.tariffs} t={t} staffContact={staffContact} currentScreen="services" />
      <div className="px-16 col gap-16 anim-stagger" style={{ paddingBottom: 24 }}>
        <div className="row" style={{ background: 'var(--surface-2)', borderRadius: 999, padding: 4 }}>
          {['lavage', 'esthetique'].map(k => (
            <button key={k} onClick={() => setTab(k)} className={k === 'esthetique' && tab !== k ? 'gold-tab-pulse' : ''} style={{
              flex: 1, padding: '11px 16px', borderRadius: 999,
              background: tab === k
                ? (k === 'esthetique' ? 'linear-gradient(135deg, var(--sun), var(--gold))' : 'var(--surface)')
                : (k === 'esthetique' ? 'linear-gradient(135deg, color-mix(in srgb, var(--sun) 36%, transparent), color-mix(in srgb, var(--gold) 30%, transparent))' : 'transparent'),
              color: tab === k
                ? (k === 'esthetique' ? '#19201a' : 'var(--text)')
                : (k === 'esthetique' ? 'var(--gold-deep)' : 'var(--text-2)'),
              fontWeight: tab === k ? 700 : 600, fontSize: 13.5,
              letterSpacing: '-0.005em',
              boxShadow: tab === k
                ? '0 1px 2px rgba(14,42,42,0.05), 0 4px 8px -2px rgba(14,42,42,0.06)'
                : 'none',
              transition: 'background 0.22s var(--ease-soft), color 0.22s var(--ease-soft), box-shadow 0.22s var(--ease-soft)',
            }}>{t[k]}</button>
          ))}
        </div>

        <div className="card-soft" style={{ padding: 14, borderRadius: 18 }}>
          <div className="row gap-10 mb-10" style={{ alignItems: 'flex-start' }}>
            <Icons.Leaf size={20} style={{ color: 'var(--accent)', marginTop: 1 }} />
            <div className="t-muted" style={{ flex: 1, fontSize: 12.5 }}>
              <strong style={{ color: 'var(--text)' }}>{t.vehicleTariffSelector || 'Tarif selon votre véhicule'}</strong><br/>
              {t.vehicleTariffSelectorSub || 'Sélectionnez un véhicule enregistré ou une catégorie pour voir directement le bon prix.'}
            </div>
          </div>
          {tariffVehicles.length > 0 && (
            <div className="row wrap gap-8 mb-10">
              {tariffVehicles.map((v, i) => {
                const selectedVehicle = selectedCategory === v.category;
                return (
                  <button key={'vehicle-' + i} type="button" className="chip"
                    onClick={() => setSelectedCategory(v.category)}
                    style={{
                      cursor: 'pointer', padding: '8px 11px',
                      borderColor: selectedVehicle ? 'var(--primary)' : 'var(--border)',
                      background: selectedVehicle ? 'var(--primary-soft)' : 'var(--chip-bg)',
                      color: selectedVehicle ? 'var(--primary-soft-text)' : 'var(--text-2)',
                      fontWeight: selectedVehicle ? 800 : 650,
                    }}>
                    {v.label || [v.make, v.color].filter(Boolean).join(' · ') || (v.category_label || v.category)}
                  </button>
                );
              })}
            </div>
          )}
          <div className="row gap-8">
            {TARIFF_CATEGORIES.map((category) => {
              const selected = selectedCategory === category;
              return (
                <button key={category} type="button" onClick={() => setSelectedCategory(category)}
                  style={{
                    flex: 1, padding: '10px 8px', borderRadius: 12,
                    background: selected ? 'var(--primary)' : 'var(--surface)',
                    color: selected ? 'var(--primary-text)' : 'var(--text)',
                    border: `1px solid ${selected ? 'var(--primary)' : 'var(--border)'}`,
                    fontWeight: 800, textAlign: 'center',
                    boxShadow: selected ? '0 8px 18px -10px color-mix(in srgb, var(--primary) 55%, transparent)' : 'none',
                  }}>
                  <div>{category}</div>
                  <div className="t-tiny" style={{ color: selected ? 'rgba(255,255,255,0.78)' : 'var(--text-3)' }}>
                    {category === 'A' ? 'Citadine' : category === 'B' ? 'Berline/SUV' : 'Grand SUV'}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {isDetailingTab && (
          <div className="card-soft" style={{
            padding: 14, borderRadius: 18,
            display: 'flex', gap: 10, alignItems: 'center',
            border: '1px solid color-mix(in srgb, var(--gold) 45%, transparent)',
            background: 'linear-gradient(135deg, color-mix(in srgb, var(--sun) 18%, var(--surface)), color-mix(in srgb, var(--gold) 14%, var(--surface)))',
          }}>
            <Icons.Sparkle size={20} style={{ color: 'var(--gold-deep)' }} />
            <div className="t-muted" style={{ flex: 1, fontSize: 12.5 }}>
              <strong style={{ color: 'var(--text)' }}>{t.detailingMultiSelectTitle || 'Prestations cumulables'}</strong><br/>
              {t.detailingMultiSelectSub || 'Sélectionnez plusieurs prestations esthétique, puis prenez rendez-vous en une seule demande.'}
            </div>
          </div>
        )}

        {catalogState.loading && (
          <div className="card-soft" style={{ padding: 16, borderRadius: 18 }}>
            <div className="row gap-10">
              <Icons.Clock size={18} style={{ color: 'var(--primary)' }} />
              <div className="t-muted">{t.loadingCatalog}</div>
            </div>
          </div>
        )}

        {catalogState.error && !catalogState.loading && (
          <div className="card-soft" style={{ padding: 16, borderRadius: 18 }}>
            <div className="col gap-12">
              <div className="t-h3">{t.networkErrorTitle}</div>
              <div className="t-muted">{t.networkErrorBody}</div>
              <Btn variant="soft" onClick={() => setReloadTick((n) => n + 1)}>
                {t.retry}
              </Btn>
            </div>
          </div>
        )}

        {items.map((s, i) => {
          const selected = isDetailingTab && selectedDetailingIds.includes(s.id);
          const rawPrice = s.prices[selectedCategory] || s.prices.A || 0;
          const displayPrice = isDetailingTab ? _discountedDetailingPrice(rawPrice) : rawPrice;
          return (
            <div
              key={i}
              className="card card-elev"
              onClick={isDetailingTab ? () => toggleDetailingService(s.id) : undefined}
              role={isDetailingTab ? 'button' : undefined}
              tabIndex={isDetailingTab ? 0 : undefined}
              onKeyDown={isDetailingTab ? (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  toggleDetailingService(s.id);
                }
              } : undefined}
              style={{
                padding: 16,
                cursor: isDetailingTab ? 'pointer' : 'default',
                border: selected ? '1.5px solid var(--gold-deep)' : undefined,
                background: selected
                  ? 'linear-gradient(135deg, color-mix(in srgb, var(--sun) 16%, var(--surface)), color-mix(in srgb, var(--gold) 12%, var(--surface)))'
                  : undefined,
                boxShadow: selected
                  ? '0 0 0 1px color-mix(in srgb, var(--gold-deep) 35%, transparent), 0 10px 24px -12px color-mix(in srgb, var(--gold-deep) 55%, transparent)'
                  : undefined,
              }}>
              <div className="row between mb-8" style={{ gap: 12, alignItems: 'flex-start' }}>
                <div className="col gap-4" style={{ minWidth: 0 }}>
                  <div className="row gap-8">
                    <div style={{ fontWeight: 700, fontSize: 15.5 }}>{s.name}</div>
                    {s.popular && <span className="chip chip-primary" style={{ fontSize: 10.5, padding: '2px 8px' }}>★ {t.mostPopular}</span>}
                  </div>
                  <div className="t-muted">{s.desc}</div>
                </div>
                {isDetailingTab && (
                  <div style={{
                    width: 28, height: 28, borderRadius: 9,
                    border: `2px solid ${selected ? 'var(--gold-deep)' : 'var(--border-strong)'}`,
                    background: selected ? 'linear-gradient(135deg, var(--sun), var(--gold))' : 'transparent',
                    color: '#19201a',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    flexShrink: 0,
                  }}>
                    {selected && <Icons.Check size={15} stroke={3}/>}
                  </div>
                )}
              </div>
              <div className="row gap-6 mb-12">
                <span className="chip"><Icons.Clock size={12}/> {_durationLabel(t, s.durationMin)}</span>
                {isDetailingTab && <span className="chip chip-accent">{t.discount20 || '-20%'}</span>}
              </div>
              <div style={{
                background: 'var(--surface-2)',
                borderRadius: 12, padding: '12px 14px',
                display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
                boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.4)',
              }}>
                <span className="t-tiny" style={{ letterSpacing: '0.1em', fontWeight: 700, color: 'var(--text-2)' }}>
                  {t.category || 'Catégorie'} {selectedCategory}
                </span>
                <span className="row gap-6" style={{ alignItems: 'baseline' }}>
                  {isDetailingTab && rawPrice > displayPrice && (
                    <span style={{ fontSize: 12, color: 'var(--text-3)', textDecoration: 'line-through', fontWeight: 700 }}>{rawPrice}</span>
                  )}
                  <span className="t-num" style={{ fontWeight: 800, fontSize: 22, color: isDetailingTab ? 'var(--gold-deep)' : 'var(--text)', letterSpacing: '-0.02em' }}>
                    {displayPrice}<span style={{ fontSize: 12, color: 'var(--text-2)', marginInlineStart: 4 }}>DH</span>
                  </span>
                </span>
              </div>
              {!isDetailingTab && (
                <Btn variant="soft" block style={{ marginTop: 12 }} onClick={() => startWashBooking(s)}>
                  {t.bookCta}
                </Btn>
              )}
            </div>
          );
        })}
        {isDetailingTab && !catalogState.loading && !catalogState.error && (
          <CtaDock className="cta-dock-above-nav">
            <div className="row between mb-8" style={{ paddingInline: 4, gap: 12 }}>
              <div className="col gap-2">
                <span className="t-muted" style={{ fontSize: 13 }}>{t.selectedServices || 'Prestations sélectionnées'}</span>
                <span className="t-tiny">
                  {selectedDetailingIds.length} · {selectedDetailingTotal}<span style={{ marginInlineStart: 3 }}>DH</span>
                </span>
              </div>
              <button type="button" className="chip" onClick={() => setSelectedDetailingIds([])} disabled={!selectedDetailingIds.length}
                style={{ opacity: selectedDetailingIds.length ? 1 : 0.45 }}>
                {t.clear || 'Effacer'}
              </button>
            </div>
            <Btn block lg disabled={!selectedDetailingIds.length} onClick={startDetailingBooking}
              style={{
                opacity: selectedDetailingIds.length ? 1 : 0.45,
                background: selectedDetailingIds.length ? 'linear-gradient(135deg, var(--sun), var(--gold))' : undefined,
                color: selectedDetailingIds.length ? '#19201a' : undefined,
                animation: selectedDetailingIds.length ? 'heroCtaBounce 1.8s ease-in-out infinite' : undefined,
              }}>
              {selectedDetailingIds.length > 0
                ? ((t.bookSelectedDetailing || 'Prendre RDV avec {count} prestations').replace('{count}', selectedDetailingIds.length))
                : (t.selectDetailingFirst || 'Sélectionnez au moins une prestation')}
            </Btn>
          </CtaDock>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// PROFILE
// ─────────────────────────────────────────────────────────────
function _clearLocalAuthState() {
  const tokenKey = window.EwashAPI && window.EwashAPI._TOKEN_KEY
    ? window.EwashAPI._TOKEN_KEY
    : 'ewash.bookings_token';
  const phoneKey = window.EwashAPI && window.EwashAPI._PHONE_KEY
    ? window.EwashAPI._PHONE_KEY
    : 'ewash.phone';
  const nameKey = window.EwashAPI && window.EwashAPI._NAME_KEY
    ? window.EwashAPI._NAME_KEY
    : 'ewash.name';
  [tokenKey, phoneKey, nameKey, 'ewash.booking_draft'].forEach((key) => {
    try {
      localStorage.removeItem(key);
    } catch (err) {
      if (window.EwashLog) {
        window.EwashLog.warn('localstorage.error', { op: 'remove', key });
      }
    }
  });
}

function ProfileScreen({ t, lang, setLang, theme, setTheme, variant, setVariant, profile, staffContact, onOpenSupport, onProfileChanged, onToast, onLogout }) {
  const [confirmingAllOut, setConfirmingAllOut] = useS_h(false);
  const [confirmingDelete, setConfirmingDelete] = useS_h(false);
  const [editingProfile, setEditingProfile] = useS_h(false);
  const [editingVehicles, setEditingVehicles] = useS_h(false);
  const [editingAddresses, setEditingAddresses] = useS_h(false);
  const [logoutBusy, setLogoutBusy] = useS_h(null);
  const [deleteBusy, setDeleteBusy] = useS_h(false);
  const [deleteError, setDeleteError] = useS_h('');
  const [savedVehicles, setSavedVehicles] = useS_h(() => _readLocalArray(EWASH_PROFILE_VEHICLES_KEY));
  const [savedAddresses, setSavedAddresses] = useS_h(() => _readLocalArray(EWASH_PROFILE_ADDRESSES_KEY));
  const [notificationsOn, setNotificationsOn] = useS_h(() => {
    try { return localStorage.getItem(EWASH_NOTIFICATIONS_KEY) !== 'false'; }
    catch (_) { return true; }
  });
  const impactStats = _readImpactStats();
  const profileLitersCount = useCountUp(impactStats.litersSaved || 0, 1200, !!impactStats.litersSaved);

  const persistVehicles = (rows) => {
    setSavedVehicles(rows);
    _writeLocalArray(EWASH_PROFILE_VEHICLES_KEY, rows);
  };
  const persistAddresses = (rows) => {
    setSavedAddresses(rows);
    _writeLocalArray(EWASH_PROFILE_ADDRESSES_KEY, rows);
  };
  const saveProfileInfo = (form) => {
    const keys = _profileStorageKeys();
    try {
      localStorage.setItem(keys.name, String(form.name || '').trim());
      localStorage.setItem(keys.phone, _normalizeProfilePhone(form.phone));
    } catch (_) {
      if (window.EwashLog) window.EwashLog.warn('localstorage.error', { op: 'set', key: 'profile' });
    }
    if (window.EwashLog) window.EwashLog.info('profile.saved', {});
    if (onProfileChanged) onProfileChanged();
    if (onToast) onToast(t.profileSaved || 'Profil enregistré');
    setEditingProfile(false);
  };
  const toggleNotifications = (next) => {
    setNotificationsOn(next);
    try { localStorage.setItem(EWASH_NOTIFICATIONS_KEY, next ? 'true' : 'false'); } catch (_) {}
    if (onToast) onToast(next ? (t.notificationsEnabled || 'Notifications activées') : (t.notificationsDisabled || 'Notifications désactivées'));
  };

  const doLogout = async (scope) => {
    if (logoutBusy) return;
    setLogoutBusy(scope);
    if (window.EwashLog) window.EwashLog.info('auth.logout', { scope });
    try {
      if (!window.EwashAPI || !window.EwashAPI.revokeToken) {
        const err = new Error('revokeToken unavailable');
        err.error_code = 'api_unavailable';
        throw err;
      }
      await window.EwashAPI.revokeToken({ scope });
    } catch (err) {
      if (window.EwashLog) {
        window.EwashLog.warn('auth.logout.warn', {
          scope,
          error_code: (err && err.error_code) || 'logout_failed',
          status: err && err.status,
        });
      }
    } finally {
      _clearLocalAuthState();
      setLogoutBusy(null);
      onLogout();
    }
  };

  const doDeleteAccount = async () => {
    if (deleteBusy) return;
    setDeleteBusy(true);
    setDeleteError('');
    if (window.EwashLog) window.EwashLog.info('me.delete.attempt', {});
    try {
      if (!window.EwashAPI || !window.EwashAPI.deleteMe) {
        const err = new Error('deleteMe unavailable');
        err.error_code = 'api_unavailable';
        throw err;
      }
      await window.EwashAPI.deleteMe({ confirm: 'I confirm I want to delete my data' });
      if (window.EwashLog) window.EwashLog.info('me.delete.success', {});
      _clearLocalAuthState();
      setConfirmingDelete(false);
      setDeleteBusy(false);
      if (onToast) onToast(t.deleteAccountSuccess);
      onLogout();
      return;
    } catch (err) {
      if (window.EwashLog) {
        window.EwashLog.warn('me.delete.error', {
          error_code: (err && err.error_code) || 'delete_failed',
          status: err && err.status,
        });
      }
      setDeleteError(t.deleteAccountError);
      if (onToast) onToast(t.deleteAccountError);
      setDeleteBusy(false);
    }
  };

  return (
    <div className="app-scroll">
      <TopBar title={t.myProfile} t={t} staffContact={staffContact} currentScreen="profile" />
      <div className="px-16 col gap-20 anim-stagger" style={{ paddingBottom: 24 }}>
        {/* User card — anonymous until first booking populates name/phone. */}
        <div className="card card-elev" style={{ padding: 16, display: 'flex', gap: 14, alignItems: 'center' }}>
          <div style={{
            width: 60, height: 60, borderRadius: 18,
            background: profile.name
              ? 'linear-gradient(135deg, color-mix(in srgb, var(--primary) 92%, white) 0%, var(--primary) 50%, color-mix(in srgb, var(--primary) 70%, black) 100%)'
              : 'color-mix(in srgb, var(--primary) 12%, var(--surface))',
            color: profile.name ? 'var(--primary-text)' : 'var(--primary)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 26,
            letterSpacing: '-0.02em',
            boxShadow: profile.name
              ? 'inset 0 1px 0 rgba(255,255,255,0.25), 0 8px 18px -8px color-mix(in srgb, var(--primary) 50%, transparent)'
              : 'none',
          }}>
            {profile.name ? profile.name[0] : <Icons.User size={28} stroke={2} />}
          </div>
          <div className="col gap-2 flex-1">
            <div style={{ fontWeight: 700, fontSize: 16 }}>
              {profile.name || (t.guestLabel || 'Invité')}
            </div>
            {profile.phone ? (
              <div className="t-muted">+212 {profile.phone}</div>
            ) : (
              <div className="t-muted" style={{ fontSize: 13 }}>
                {t.guestHint || 'Réservez pour personnaliser votre profil'}
              </div>
            )}
          </div>
          <button className="icon-btn" onClick={() => setEditingProfile(true)} aria-label={t.editProfile || 'Modifier le profil'}>
            <Icons.Edit size={18}/>
          </button>
        </div>

        {/* Eco impact card */}
        <div className="card-soft" style={{
          padding: '20px 18px', borderRadius: 22,
          background: 'var(--hero-grad)', color: '#fff',
          position: 'relative', overflow: 'hidden',
          boxShadow: '0 20px 40px -16px rgba(15,120,120,0.45)',
        }}>
          <div style={{
            position: 'absolute', insetInlineEnd: -30, top: -30,
            width: 160, height: 160, borderRadius: '50%',
            background: 'radial-gradient(circle, rgba(255,255,255,0.16) 0%, transparent 65%)',
            pointerEvents: 'none',
          }}/>
          <div className="row gap-8 mb-8" style={{ position: 'relative' }}>
            <Icons.Drop size={18} />
            <div className="t-tiny" style={{ fontWeight: 700, letterSpacing: '0.12em', color: 'rgba(255,255,255,0.78)' }}>
              {(t.ecoImpact || 'Impact').toUpperCase()}
            </div>
          </div>
          <div className="row" style={{ alignItems: 'baseline', gap: 8, position: 'relative' }}>
            <div style={{
              fontFamily: 'var(--font-display)', fontWeight: 800,
              fontSize: impactStats.litersSaved ? 40 : 30,
              letterSpacing: 0,
              lineHeight: 1.0,
              fontVariantNumeric: 'tabular-nums',
            }}>
              {impactStats.litersSaved ? (
                <React.Fragment>
                  {profileLitersCount.toLocaleString('fr-FR')}<span style={{ fontSize: 22, marginInlineStart: 4, opacity: 0.85 }}>L</span>
                </React.Fragment>
              ) : (
                t.waterlessBadge || 'Sans eau'
              )}
            </div>
          </div>
          <div className="t-muted" style={{ color: 'rgba(255,255,255,0.78)', marginTop: 6, position: 'relative' }}>
            {impactStats.litersSaved ? t.waterSaved.toLowerCase() : (t.impactPendingBody || 'Vos économies réelles apparaîtront ici après vos réservations.')}
          </div>
        </div>

        <ProfileSection title={lang === 'ar' ? 'حسابي' : 'Mon compte'}>
          <ProfileRow
            icon={<Icons.CarSide size={18}/>}
            label={t.myVehicles}
            value={savedVehicles.length ? `${savedVehicles.length} ${savedVehicles.length > 1 ? (t.vehicles || 'véhicules') : (t.vehicle || 'véhicule')}` : (t.add || 'Ajouter')}
            onClick={() => setEditingVehicles(true)}
          />
          <ProfileRow
            icon={<Icons.Pin size={18}/>}
            label={t.addresses}
            value={savedAddresses.length ? String(savedAddresses.length) : (t.add || 'Ajouter')}
            onClick={() => setEditingAddresses(true)}
          />
          <ProfileRow icon={<Icons.Wallet size={18}/>} label={t.paymentMethods} value={t.paymentNote} />
        </ProfileSection>

        <ProfileSection title={t.settings}>
          <ProfileRow icon={theme === 'dark' ? <Icons.Moon size={18}/> : <Icons.Sun size={18}/>}
            label={lang === 'ar' ? 'الوضع الليلي' : 'Mode sombre'}
            right={<ProfileSwitch on={theme === 'dark'} onChange={(v) => setTheme(v ? 'dark' : 'light')}/>} />
          <ProfileRow icon={<Icons.Globe size={18}/>} label={t.language}
            right={
              <div className="row" style={{ background: 'var(--surface-2)', padding: 3, borderRadius: 999 }}>
                {[{c:'fr',l:'FR'}, {c:'ar',l:'AR'}].map(o => (
                  <button key={o.c} onClick={() => setLang(o.c)}
                    style={{
                      padding: '6px 14px', borderRadius: 999,
                      background: lang === o.c ? 'var(--primary)' : 'transparent',
                      color: lang === o.c ? 'var(--primary-text)' : 'var(--text-2)',
                      fontWeight: 700, fontSize: 12,
                    }}>{o.l}</button>
                ))}
              </div>
            } />
          <ProfileRow icon={<Icons.Sparkle size={18}/>} label={lang === 'ar' ? 'الأسلوب' : 'Style'}
            right={
              <div className="row" style={{ background: 'var(--surface-2)', padding: 3, borderRadius: 999 }}>
                {[{c:'eco',l:'Eco'},{c:'premium',l:'Premium'}].map(o => (
                  <button key={o.c} onClick={() => setVariant(o.c)}
                    style={{
                      padding: '6px 12px', borderRadius: 999,
                      background: variant === o.c ? 'var(--primary)' : 'transparent',
                      color: variant === o.c ? 'var(--primary-text)' : 'var(--text-2)',
                      fontWeight: 700, fontSize: 11.5,
                    }}>{o.l}</button>
                ))}
              </div>
            } />
          <ProfileRow icon={<Icons.Bell size={18}/>} label={t.notifications}
            right={<ProfileSwitch on={notificationsOn} onChange={toggleNotifications} />} />
        </ProfileSection>

        <ProfileSection>
          <ProfileRow icon={<Icons.Message size={18}/>} label={t.helpCenter} onClick={onOpenSupport || (() => _openTeamChat(t, staffContact))} />
          {profile.name && (
            <ProfileRow
              icon={<Icons.LogOut size={18}/>}
              label={logoutBusy === 'current' ? t.logoutInProgress : t.logout}
              onClick={() => doLogout('current')}
              danger
              disabled={!!logoutBusy}
            />
          )}
          {profile.name && (
            <ProfileRow
              icon={<Icons.Shield size={18}/>}
              label={t.logoutEverywhere}
              onClick={() => setConfirmingAllOut(true)}
              danger
              disabled={!!logoutBusy}
            />
          )}
        </ProfileSection>

        {profile.name && (
          <ProfileSection title={t.dangerZoneTitle}>
            <ProfileRow
              icon={<Icons.Close size={18}/>}
              label={t.deleteAccount}
              onClick={() => {
                setDeleteError('');
                setConfirmingDelete(true);
              }}
              danger
              disabled={!!logoutBusy || deleteBusy}
            />
          </ProfileSection>
        )}

        <div className="text-center t-tiny" style={{ paddingBlock: 8 }}>
          Ewash · {t.appVersion} 1.0.0 (Casablanca)
        </div>
      </div>
      <LogoutEverywhereSheet
        open={confirmingAllOut}
        t={t}
        busy={logoutBusy === 'all'}
        onCancel={() => setConfirmingAllOut(false)}
        onConfirm={() => {
          setConfirmingAllOut(false);
          doLogout('all');
        }}
      />
      <DeleteAccountConfirmSheet
        open={confirmingDelete}
        t={t}
        busy={deleteBusy}
        error={deleteError}
        onCancel={() => {
          if (deleteBusy) return;
          setConfirmingDelete(false);
        }}
        onConfirm={doDeleteAccount}
      />
      <ProfileInfoSheet
        open={editingProfile}
        t={t}
        profile={profile}
        onCancel={() => setEditingProfile(false)}
        onSave={saveProfileInfo}
      />
      <ProfileVehiclesSheet
        open={editingVehicles}
        t={t}
        vehicles={savedVehicles}
        onCancel={() => setEditingVehicles(false)}
        onSave={persistVehicles}
      />
      <ProfileAddressesSheet
        open={editingAddresses}
        t={t}
        addresses={savedAddresses}
        onCancel={() => setEditingAddresses(false)}
        onSave={persistAddresses}
      />
    </div>
  );
}

function ProfileSection({ title, children }) {
  return (
    <div className="col gap-8">
      {title && <div className="t-tiny" style={{
        textTransform: 'uppercase', letterSpacing: '0.1em', fontWeight: 700,
        color: 'var(--text-3)', paddingInlineStart: 4,
      }}>{title}</div>}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {children}
      </div>
    </div>
  );
}

function ProfileRow({ icon, label, value, right, onClick, danger, disabled }) {
  return (
    <button onClick={disabled ? undefined : onClick} disabled={disabled} style={{
      display: 'flex', alignItems: 'center', gap: 14,
      padding: '14px 16px', width: '100%',
      borderBottom: '1px solid var(--border)',
      textAlign: 'inherit', cursor: disabled ? 'not-allowed' : onClick ? 'pointer' : 'default',
      color: danger ? 'var(--danger)' : 'var(--text)',
      opacity: disabled ? 0.58 : 1,
    }}>
      <div style={{
        width: 36, height: 36, borderRadius: 10,
        background: danger ? 'rgba(229,72,77,0.12)' : 'var(--surface-2)',
        color: danger ? 'var(--danger)' : 'var(--text)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
      }}>{icon}</div>
      <div className="flex-1" style={{ fontWeight: 600, fontSize: 14.5 }}>{label}</div>
      {value && <div className="t-muted" style={{ fontSize: 13 }}>{value}</div>}
      {right || (onClick && !danger && <Icons.ChevronRight size={16} style={{ color: 'var(--text-3)' }}/>)}
    </button>
  );
}

function ProfileInfoSheet({ open, t, profile, onSave, onCancel }) {
  const [form, setForm] = useS_h({ name: '', phone: '' });
  useE_h(() => {
    if (open) setForm({ name: (profile && profile.name) || '', phone: (profile && profile.phone) || '' });
  }, [open, profile && profile.name, profile && profile.phone]);
  return (
    <Sheet open={open} onClose={onCancel}>
      <div className="col gap-16" style={{ padding: '8px 16px 22px' }}>
        <div className="col gap-6">
          <div className="t-h1">{t.editProfile || 'Modifier le profil'}</div>
          <div className="t-muted">{t.editProfileSub || 'Mettez à jour vos informations personnelles.'}</div>
        </div>
        <Field label={t.name || 'Nom'}>
          <input className="input" value={form.name}
            onChange={(event) => setForm(Object.assign({}, form, { name: event.target.value }))}
            placeholder="Omar" />
        </Field>
        <Field label={t.phone || 'Téléphone'} hint="Format : +212 6…">
          <input className="input" inputMode="tel" value={form.phone}
            onChange={(event) => setForm(Object.assign({}, form, { phone: event.target.value }))}
            placeholder="611204502" />
        </Field>
        <div className="row gap-8">
          <Btn variant="ghost" style={{ flex: 1 }} onClick={onCancel}>{t.cancel}</Btn>
          <Btn style={{ flex: 1 }} onClick={() => onSave(form)}>{t.save || 'Enregistrer'}</Btn>
        </div>
      </div>
    </Sheet>
  );
}

function ProfileVehiclesSheet({ open, t, vehicles, onSave, onCancel }) {
  const emptyForm = { category: 'A', make: '', color: '', plate: '' };
  const [rows, setRows] = useS_h([]);
  const [editingId, setEditingId] = useS_h(null);
  const [form, setForm] = useS_h(emptyForm);
  useE_h(() => {
    if (!open) return;
    setRows((vehicles || []).map((row) => Object.assign({ id: _profileRowId('veh') }, row)));
    setEditingId(null);
    setForm(emptyForm);
  }, [open, vehicles]);
  const saveCurrent = () => {
    if (!form.make.trim() && !form.color.trim() && !form.plate.trim()) return;
    const clean = {
      id: editingId || _profileRowId('veh'),
      category: form.category || 'A',
      make: form.make.trim(),
      color: form.color.trim(),
      plate: form.plate.trim(),
    };
    setRows((prev) => editingId
      ? prev.map((row) => row.id === editingId ? clean : row)
      : prev.concat(clean));
    setEditingId(null);
    setForm(emptyForm);
  };
  const editRow = (row) => {
    setEditingId(row.id);
    setForm({ category: row.category || 'A', make: row.make || '', color: row.color || '', plate: row.plate || '' });
  };
  const removeRow = (id) => {
    setRows((prev) => prev.filter((row) => row.id !== id));
    if (editingId === id) { setEditingId(null); setForm(emptyForm); }
  };
  return (
    <Sheet open={open} onClose={onCancel}>
      <div className="col gap-16" style={{ padding: '8px 16px 22px' }}>
        <div className="col gap-6">
          <div className="t-h1">{t.myVehicles || 'Mes véhicules'}</div>
          <div className="t-muted">{t.editVehiclesSub || 'Ajoutez ou modifiez les véhicules à réutiliser en réservation.'}</div>
        </div>
        <div className="col gap-8">
          {rows.length ? rows.map((row) => (
            <div key={row.id} className="card-soft row gap-10" style={{ padding: 12, borderRadius: 14 }}>
              <Icons.CarSide size={18} />
              <div className="flex-1 col gap-2" style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700 }}>{_vehicleDisplayLabel(row)}</div>
                <div className="t-tiny">{row.category}{row.plate ? ' · ' + row.plate : ''}</div>
              </div>
              <button className="chip" onClick={() => editRow(row)}>{t.edit || 'Modifier'}</button>
              <button className="chip" onClick={() => removeRow(row.id)}>{t.delete || 'Supprimer'}</button>
            </div>
          )) : <div className="card-soft text-center t-muted" style={{ padding: 16 }}>{t.noVehicles || 'Aucun véhicule enregistré'}</div>}
        </div>
        <div className="card" style={{ padding: 14 }}>
          <div className="col gap-10">
            <div className="row gap-8">
              {['A', 'B', 'C', 'MOTO'].map((category) => (
                <button key={category} className="chip" onClick={() => setForm(Object.assign({}, form, { category }))}
                  style={{
                    borderColor: form.category === category ? 'var(--primary)' : 'var(--border)',
                    background: form.category === category ? 'var(--primary-soft)' : 'var(--chip-bg)',
                    color: form.category === category ? 'var(--primary-soft-text)' : 'var(--text-2)',
                  }}>{category}</button>
              ))}
            </div>
            <input className="input" value={form.make} onChange={(event) => setForm(Object.assign({}, form, { make: event.target.value }))} placeholder={t.makeModelPh || 'Marque / modèle'} />
            <input className="input" value={form.color} onChange={(event) => setForm(Object.assign({}, form, { color: event.target.value }))} placeholder={t.colorPh || 'Couleur'} />
            <input className="input" value={form.plate} onChange={(event) => setForm(Object.assign({}, form, { plate: event.target.value }))} placeholder="Plaque (optionnel)" />
            <Btn variant="soft" onClick={saveCurrent}>{editingId ? (t.update || 'Mettre à jour') : (t.addVehicle || 'Ajouter le véhicule')}</Btn>
          </div>
        </div>
        <div className="row gap-8">
          <Btn variant="ghost" style={{ flex: 1 }} onClick={onCancel}>{t.cancel}</Btn>
          <Btn style={{ flex: 1 }} onClick={() => { onSave(rows); onCancel(); }}>{t.save || 'Enregistrer'}</Btn>
        </div>
      </div>
    </Sheet>
  );
}

function ProfileAddressesSheet({ open, t, addresses, onSave, onCancel }) {
  const emptyForm = { label: '', address: '', details: '' };
  const [rows, setRows] = useS_h([]);
  const [editingId, setEditingId] = useS_h(null);
  const [form, setForm] = useS_h(emptyForm);
  useE_h(() => {
    if (!open) return;
    setRows((addresses || []).map((row) => Object.assign({ id: _profileRowId('addr') }, row)));
    setEditingId(null);
    setForm(emptyForm);
  }, [open, addresses]);
  const saveCurrent = () => {
    if (!form.label.trim() && !form.address.trim()) return;
    const clean = {
      id: editingId || _profileRowId('addr'),
      label: form.label.trim(),
      address: form.address.trim(),
      details: form.details.trim(),
    };
    setRows((prev) => editingId
      ? prev.map((row) => row.id === editingId ? clean : row)
      : prev.concat(clean));
    setEditingId(null);
    setForm(emptyForm);
  };
  const editRow = (row) => {
    setEditingId(row.id);
    setForm({ label: row.label || '', address: row.address || '', details: row.details || '' });
  };
  const removeRow = (id) => {
    setRows((prev) => prev.filter((row) => row.id !== id));
    if (editingId === id) { setEditingId(null); setForm(emptyForm); }
  };
  return (
    <Sheet open={open} onClose={onCancel}>
      <div className="col gap-16" style={{ padding: '8px 16px 22px' }}>
        <div className="col gap-6">
          <div className="t-h1">{t.addresses || 'Mes adresses'}</div>
          <div className="t-muted">{t.editAddressesSub || 'Ajoutez vos adresses fréquentes pour les prochains rendez-vous à domicile.'}</div>
        </div>
        <div className="col gap-8">
          {rows.length ? rows.map((row) => (
            <div key={row.id} className="card-soft row gap-10" style={{ padding: 12, borderRadius: 14 }}>
              <Icons.Pin size={18} />
              <div className="flex-1 col gap-2" style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700 }}>{_addressDisplayLabel(row)}</div>
                {row.details && <div className="t-tiny">{row.details}</div>}
              </div>
              <button className="chip" onClick={() => editRow(row)}>{t.edit || 'Modifier'}</button>
              <button className="chip" onClick={() => removeRow(row.id)}>{t.delete || 'Supprimer'}</button>
            </div>
          )) : <div className="card-soft text-center t-muted" style={{ padding: 16 }}>{t.noAddresses || 'Aucune adresse enregistrée'}</div>}
        </div>
        <div className="card" style={{ padding: 14 }}>
          <div className="col gap-10">
            <input className="input" value={form.label} onChange={(event) => setForm(Object.assign({}, form, { label: event.target.value }))} placeholder={t.addressLabelPh || 'Maison, bureau…'} />
            <input className="input" value={form.address} onChange={(event) => setForm(Object.assign({}, form, { address: event.target.value }))} placeholder={t.addressPh || 'Adresse'} />
            <textarea className="input" rows={3} value={form.details} onChange={(event) => setForm(Object.assign({}, form, { details: event.target.value }))} placeholder={t.addressDetailsPh || 'Détails d’accès'} />
            <Btn variant="soft" onClick={saveCurrent}>{editingId ? (t.update || 'Mettre à jour') : (t.addAddress || 'Ajouter l’adresse')}</Btn>
          </div>
        </div>
        <div className="row gap-8">
          <Btn variant="ghost" style={{ flex: 1 }} onClick={onCancel}>{t.cancel}</Btn>
          <Btn style={{ flex: 1 }} onClick={() => { onSave(rows); onCancel(); }}>{t.save || 'Enregistrer'}</Btn>
        </div>
      </div>
    </Sheet>
  );
}

function LogoutEverywhereSheet({ open, t, busy, onConfirm, onCancel }) {
  return (
    <Sheet open={open} onClose={onCancel}>
      <div className="logout-sheet col gap-16">
        <div className="logout-sheet-icon">
          <Icons.Shield size={28}/>
        </div>
        <div className="col gap-6">
          <div className="t-h1">{t.logoutEverywhereTitle}</div>
          <div className="t-muted">{t.logoutEverywhereBody}</div>
        </div>
        <div className="row gap-8">
          <Btn variant="ghost" style={{ flex: 1 }} onClick={onCancel} disabled={busy}>
            {t.cancel}
          </Btn>
          <Btn variant="danger" style={{ flex: 1 }} onClick={onConfirm} disabled={busy}>
            {busy ? t.logoutInProgress : t.logoutEverywhereConfirm}
          </Btn>
        </div>
      </div>
    </Sheet>
  );
}

function DeleteAccountConfirmSheet({ open, t, busy, error, onConfirm, onCancel }) {
  const [typed, setTyped] = useS_h('');
  const requiredPhrase = t.deleteConfirmPhrase || 'SUPPRIMER';
  const normalizedRequired = String(requiredPhrase).trim().toUpperCase();
  const matches = typed.trim().toUpperCase() === normalizedRequired;

  useE_h(() => {
    if (open) setTyped('');
  }, [open]);

  return (
    <Sheet open={open} onClose={busy ? undefined : onCancel}>
      <div className="delete-sheet col gap-16">
        <div className="delete-sheet-icon">
          <Icons.Close size={28}/>
        </div>
        <div className="col gap-6">
          <div className="t-h1">{t.deleteAccountTitle}</div>
          <div className="delete-warning-title">{t.deleteAccountWarningTitle}</div>
          <div className="t-muted">{t.deleteAccountWarningBody}</div>
        </div>
        <ul className="delete-warning-list">
          <li>{t.deleteAccountConsequence1}</li>
          <li>{t.deleteAccountConsequence2}</li>
          <li>{t.deleteAccountConsequence3}</li>
        </ul>
        <label className="delete-confirm-label">
          {String(t.deleteConfirmPrompt || '').replace('{phrase}', requiredPhrase)}
        </label>
        <input
          className="input delete-confirm-input"
          type="text"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          autoCorrect="off"
          autoCapitalize="characters"
          spellCheck={false}
        />
        {error && <div className="delete-error">{error}</div>}
        <div className="row gap-8">
          <Btn variant="ghost" style={{ flex: 1 }} onClick={onCancel} disabled={busy}>
            {t.cancel}
          </Btn>
          <Btn variant="danger" style={{ flex: 1 }} onClick={matches ? onConfirm : undefined} disabled={!matches || busy}>
            {busy ? t.deleteAccountDeleting : t.deleteAccountConfirm}
          </Btn>
        </div>
      </div>
    </Sheet>
  );
}

function ProfileSwitch({ on, onChange }) {
  return (
    <button onClick={() => onChange && onChange(!on)} style={{
      width: 46, height: 28, borderRadius: 99,
      background: on ? 'var(--primary)' : 'var(--border-strong)',
      position: 'relative',
      transition: 'background 0.25s var(--ease-soft)',
      flexShrink: 0,
      boxShadow: on
        ? 'inset 0 1px 1px rgba(0,0,0,0.06), 0 4px 10px -4px color-mix(in srgb, var(--primary) 35%, transparent)'
        : 'inset 0 1px 2px rgba(0,0,0,0.05)',
    }}>
      <span style={{
        position: 'absolute', top: 3, insetInlineStart: on ? 21 : 3,
        width: 22, height: 22, borderRadius: 99,
        background: '#fff',
        transition: 'inset-inline-start 0.28s var(--ease-spring), box-shadow 0.2s',
        boxShadow: '0 1px 2px rgba(0,0,0,0.2), 0 3px 6px rgba(0,0,0,0.15)',
      }}/>
    </button>
  );
}

// ─────────────────────────────────────────────────────────────
// SUPPORT (chat with team)
// ─────────────────────────────────────────────────────────────
function SupportScreen({ t, onBack, theme, staffContact }) {
  const [messages, setMessages] = useS_h([
    { from: 'bot', text: t.supportTitle === 'Parler à l\'équipe'
      ? 'Bonjour ! Comment puis-je vous aider aujourd\'hui ?'
      : 'مرحباً! كيف يمكنني مساعدتك اليوم؟',
      time: '10:24' },
  ]);
  const [draft, setDraft] = useS_h('');
  const send = () => {
    if (!draft.trim()) return;
    const newMsgs = [...messages, { from: 'me', text: draft, time: '10:25' }];
    setMessages(newMsgs);
    setDraft('');
    setTimeout(() => {
      setMessages(prev => [...prev, {
        from: 'bot', time: '10:26',
        text: t.supportTitle === 'Parler à l\'équipe'
          ? 'Merci, votre message a bien été transmis à notre équipe. Un conseiller va vous répondre rapidement.'
          : 'شكراً، تم إرسال رسالتك إلى فريقنا. سيتواصل معك مستشار قريباً.',
      }]);
    }, 800);
  };
  return (
    // minHeight: 0 needed for the .app-scroll child to scroll properly when
    // the message list exceeds viewport — same flex-column-min-height fix
    // as BookingFlow.
    <div className="col" style={{ flex: 1, minHeight: 0, background: 'var(--bg)' }}>
      <TopBar onBack={onBack}
        title={t.supportTitle}
        t={t}
        staffContact={staffContact}
        currentScreen="support"
        subtitle={<span style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          color: 'var(--accent-soft-text)',
        }}>
          <span style={{ width: 6, height: 6, borderRadius: 99, background: 'var(--accent)' }}/>
          {t.supportSub}
        </span>}/>
      <div className="flex-1 app-scroll px-16 col gap-12" style={{ paddingTop: 8, paddingBottom: 12 }}>
        {messages.map((m, i) => (
          <div key={i} style={{
            display: 'flex',
            justifyContent: m.from === 'me' ? 'flex-end' : 'flex-start',
          }}>
            <div style={{
              maxWidth: '75%',
              background: m.from === 'me' ? 'var(--primary)' : 'var(--surface)',
              color: m.from === 'me' ? 'var(--primary-text)' : 'var(--text)',
              borderRadius: m.from === 'me' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
              padding: '10px 14px', fontSize: 14, lineHeight: 1.4,
              border: m.from === 'me' ? 'none' : '1px solid var(--border)',
            }}>
              {m.text}
              <div className="t-tiny" style={{
                marginTop: 4, textAlign: 'end',
                color: m.from === 'me' ? 'rgba(255,255,255,0.7)' : 'var(--text-3)',
              }}>{m.time}</div>
            </div>
          </div>
        ))}
      </div>
      <div style={{
        display: 'flex', gap: 8, padding: '10px 12px',
        borderTop: '1px solid var(--border)',
        background: 'var(--surface)',
      }}>
        <input className="input" placeholder={t.typeMessage}
          value={draft} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
          style={{ flex: 1, background: 'var(--surface-2)', borderColor: 'transparent' }}/>
        <button onClick={send} style={{
          width: 48, height: 48, borderRadius: 99,
          background: 'var(--primary)', color: 'var(--primary-text)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0,
        }}>
          <Icons.Send size={20}/>
        </button>
      </div>
    </div>
  );
}

Object.assign(window, { HomeScreen, BookingsScreen, ServicesScreen, ProfileScreen, SupportScreen });
