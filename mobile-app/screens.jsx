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

function _readImpactStats() {
  const source = typeof window !== 'undefined' ? window.EWASH_IMPACT_STATS : null;
  return {
    litersSaved: _positiveNumber(source && source.liters_saved),
    washCount: _positiveNumber(source && source.wash_count),
  };
}

// ─────────────────────────────────────────────────────────────
// HOME
// ─────────────────────────────────────────────────────────────
function HomeScreen({ t, lang, openBooking, gotoSupport, gotoTariffs, theme, variant, profile, staffContact }) {
  const impactStats = _readImpactStats();
  const litersCount = useCountUp(impactStats.litersSaved || 0, 1400, !!impactStats.litersSaved);
  const washCount = useCountUp(impactStats.washCount || 0, 900, !!impactStats.washCount);
  return (
    <div className="app-scroll">
      <div className="appbar">
        <div className="row gap-10">
          <Icons.Logo size={30} style={{ color: 'var(--primary)' }} />
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
          <button className="icon-btn" aria-label="notifications">
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
          <div className="row gap-8" style={{ position: 'relative', zIndex: 1, marginBottom: 12 }}>
            <span className="chip" style={{
              background: 'rgba(255,255,255,0.14)',
              color: '#fff', border: '1px solid rgba(255,255,255,0.18)',
            }}>
              <Icons.Leaf size={13}/> Sans eau · 100%
            </span>
          </div>
          <div style={{
            fontFamily: 'var(--font-display)', fontWeight: 800,
            fontSize: 28, lineHeight: 1.05, color: '#fff',
            marginBottom: 8, position: 'relative', zIndex: 1,
            letterSpacing: '-0.02em', maxWidth: 240,
          }}>
            {lang === 'ar' ? 'سيارة نظيفة، بدون قطرة ماء.' : 'Voiture propre,\nzéro goutte d’eau.'}
          </div>
          <div style={{
            fontFamily: 'var(--font-display)', fontWeight: 700,
            fontSize: 18, color: '#fff',
            letterSpacing: '-0.01em',
            marginBottom: 6, position: 'relative', zIndex: 1,
            opacity: 0.95,
          }}>
            e-wash
          </div>
          <div style={{ color: 'rgba(255,255,255,0.78)', fontSize: 13.5, marginBottom: 18, position: 'relative', zIndex: 1, maxWidth: 250 }}>
            {t.tagline}
          </div>
          <button onClick={openBooking}
            className="press"
            style={{
              background: variant === 'premium' ? 'var(--gold)' : '#fff',
              color: variant === 'premium' ? '#0a0a0a' : 'var(--primary)',
              border: 'none', borderRadius: 999,
              padding: '14px 24px', fontWeight: 700, fontSize: 15,
              letterSpacing: '-0.01em',
              display: 'inline-flex', alignItems: 'center', gap: 8,
              position: 'relative', zIndex: 1, cursor: 'pointer',
              boxShadow: '0 8px 22px -8px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.6)',
            }}>
            {t.bookCta}
            <Icons.ChevronRight size={18} stroke={2.5} />
          </button>
          {/* Brand mark — large, partially transparent so it reads as a
              backdrop element rather than competing with the headline. The
              .water-glyph class supplies the bottom-right pinning + drift
              animation already used by the SVG it replaces. */}
          <img
            className="water-glyph"
            src="assets/ewash_1024x1024_transparent.png"
            alt=""
            aria-hidden="true"
          />
        </div>

        {/* QUICK STATS */}
        <div className="row gap-10">
          <div className="card" style={{ flex: 1, padding: 14, borderRadius: 18 }}>
            <div className="row gap-6 mb-8">
              <Icons.Drop size={16} style={{ color: 'var(--primary)' }} />
              <span className="t-tiny" style={{ color: 'var(--text-2)', fontWeight: 600 }}>
                {impactStats.litersSaved ? t.waterSaved : (t.waterlessMethod || 'Méthode')}
              </span>
            </div>
            <div className="t-num" style={{ fontWeight: 800, fontSize: impactStats.litersSaved ? 26 : 22, color: 'var(--text)' }}>
              {impactStats.litersSaved ? (
                <React.Fragment>
                  {litersCount.toLocaleString('fr-FR')}<span style={{ fontSize: 14, color: 'var(--text-2)', marginInlineStart: 4 }}>L</span>
                </React.Fragment>
              ) : (
                t.waterlessBadge || 'Sans eau'
              )}
            </div>
          </div>
          <div className="card" style={{ flex: 1, padding: 14, borderRadius: 18 }}>
            <div className="row gap-6 mb-8">
              <Icons.Sparkle size={16} style={{ color: 'var(--accent)' }} />
              <span className="t-tiny" style={{ color: 'var(--text-2)', fontWeight: 600 }}>
                {impactStats.washCount ? (t.washMetric || 'Lavages') : (t.ecoImpact || 'Impact')}
              </span>
            </div>
            <div className="t-num" style={{ fontWeight: 800, fontSize: 26, color: 'var(--text)' }}>
              {impactStats.washCount ? washCount : '—'}
            </div>
            {!impactStats.washCount && (
              <div className="t-tiny" style={{ color: 'var(--text-3)', fontWeight: 600, marginTop: 2 }}>
                {t.impactPending || 'Mesure en cours'}
              </div>
            )}
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
  const phone = staffContact && staffContact.whatsapp_phone;
  const action = intent === 'edit' ? 'modifier' : 'suivre';
  const text = "Bonjour, je souhaite " + action + " ma réservation Ewash " + ((booking && booking.ref) || '') + ".";
  const url = phone ? _waLinkFor(phone, text) : ('https://wa.me/?text=' + encodeURIComponent(text));
  if (url) {
    window.open(url, '_blank');
    return;
  }
  if (fallback) fallback();
}

// Home "Parler à l'équipe" tile. Opens the user's WhatsApp with a prefilled
// message to the staff phone returned from /api/v1/bootstrap. If the bootstrap
// hasn't populated staffContact yet (shouldn't happen in practice — bootstrap
// runs at app launch in app.jsx), falls back to the generic wa.me URL that
// lets the user pick a contact themselves.
function _openTeamChat(t, staffContact) {
  if (window.EwashLog) window.EwashLog.info('home.talk_team.opened', {});
  const phone = staffContact && staffContact.whatsapp_phone;
  const text = (t && t.talkTeamMessage) ||
    "Bonjour Ewash, je souhaite discuter avec votre équipe.";
  const url = phone
    ? _waLinkFor(phone, text)
    : ('https://wa.me/?text=' + encodeURIComponent(text));
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

        {uiState === 'list' && bookings.map(function (b) {
          return (
            <BookingCard
              key={b.ref}
              booking={b}
              onTap={function () { setSelectedRef(b.ref); }}
            />
          );
        })}
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
    // Even without a configured staff_contact, the customer can still share a
    // ready-made message via the WhatsApp app picker — the wa.me URL works
    // without a target number, falling back to the app's contact picker.
    const phone = staffContact && staffContact.whatsapp_phone;
    const text = "Bonjour, ma réservation Ewash " + booking.ref + " le " + (booking.date_label || '') + " à " + (booking.slot_label || '') + ". Pouvez-vous me donner plus d'infos ?";
    const url = phone ? _waLinkFor(phone, text) : ('https://wa.me/?text=' + encodeURIComponent(text));
    if (!url) return;
    if (window.EwashLog) window.EwashLog.info('bookings.share', { ref: booking.ref, channel: 'whatsapp' });
    window.open(url, '_blank');
  };

  const contactSupport = function () {
    const phone = staffContact && staffContact.whatsapp_phone;
    if (!phone) return;
    const text = "Bonjour, j'ai besoin d'aide concernant ma réservation " + booking.ref + ".";
    const url = _waLinkFor(phone, text);
    if (!url) return;
    if (window.EwashLog) window.EwashLog.info('bookings.contact_support', { ref: booking.ref });
    window.open(url, '_blank');
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
            name: service.name,
            desc: service.desc,
            durationMin: service.duration_min || service.durationMin || 45,
            prices: {},
          };
          grouped[screenBucket].set(service.id, row);
        }
        row.prices[category] = service.price_dh || 0;
      });
    });
  });
  return {
    lavage: Array.from(grouped.lavage.values()),
    esthetique: Array.from(grouped.esthetique.values()),
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

  const items = tab === 'lavage' ? catalogState.lavage : catalogState.esthetique;
  return (
    <div className="app-scroll">
      <TopBar title={t.tariffs} t={t} staffContact={staffContact} currentScreen="services" />
      <div className="px-16 col gap-16 anim-stagger" style={{ paddingBottom: 24 }}>
        <div className="row" style={{ background: 'var(--surface-2)', borderRadius: 999, padding: 4 }}>
          {['lavage', 'esthetique'].map(k => (
            <button key={k} onClick={() => setTab(k)} style={{
              flex: 1, padding: '11px 16px', borderRadius: 999,
              background: tab === k ? 'var(--surface)' : 'transparent',
              color: tab === k ? 'var(--text)' : 'var(--text-2)',
              fontWeight: tab === k ? 700 : 600, fontSize: 13.5,
              letterSpacing: '-0.005em',
              boxShadow: tab === k
                ? '0 1px 2px rgba(14,42,42,0.05), 0 4px 8px -2px rgba(14,42,42,0.06)'
                : 'none',
              transition: 'background 0.22s var(--ease-soft), color 0.22s var(--ease-soft), box-shadow 0.22s var(--ease-soft)',
            }}>{t[k]}</button>
          ))}
        </div>

        <div className="card-soft" style={{
          padding: 14, borderRadius: 18,
          display: 'flex', gap: 10, alignItems: 'center',
        }}>
          <Icons.Leaf size={20} style={{ color: 'var(--accent)' }} />
          <div className="t-muted" style={{ flex: 1, fontSize: 12.5 }}>
            <strong style={{ color: 'var(--text)' }}>{t.ecoTag}</strong><br/>
            A : Citadine · B : Petite berline / SUV · C : Grande berline / SUV
          </div>
        </div>

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
          const categoryPrices = TARIFF_CATEGORIES.map(c => s.prices[c]);
          const flat = categoryPrices.every(price => price === categoryPrices[0]);
          return (
            <div key={i} className="card card-elev" style={{ padding: 16 }}>
              <div className="row between mb-8">
                <div className="col gap-4">
                  <div className="row gap-8">
                    <div style={{ fontWeight: 700, fontSize: 15.5 }}>{s.name}</div>
                    {s.popular && <span className="chip chip-primary" style={{ fontSize: 10.5, padding: '2px 8px' }}>★ {t.mostPopular}</span>}
                  </div>
                  <div className="t-muted">{s.desc}</div>
                </div>
              </div>
              <div className="row gap-6 mb-12">
                <span className="chip"><Icons.Clock size={12}/> {s.durationMin} {t.min}</span>
              </div>
              {flat ? (
                <div style={{
                  background: 'var(--surface-2)',
                  borderRadius: 12, padding: '12px 14px',
                  display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
                  boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.4)',
                }}>
                  <span className="t-tiny" style={{ letterSpacing: '0.1em', fontWeight: 700, color: 'var(--text-2)' }}>
                    TOUTES CATÉGORIES
                  </span>
                  <span className="t-num" style={{ fontWeight: 800, fontSize: 22, color: 'var(--text)', letterSpacing: '-0.02em' }}>
                    {s.prices.A}<span style={{ fontSize: 12, color: 'var(--text-2)', marginInlineStart: 4 }}>DH</span>
                  </span>
                </div>
              ) : (
                <div className="row gap-8">
                  {TARIFF_CATEGORIES.map(c => (
                    <div key={c} className="flex-1" style={{
                      background: 'var(--surface-2)',
                      borderRadius: 12, padding: '10px 8px',
                      textAlign: 'center',
                      boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.4)',
                    }}>
                      <div className="t-tiny" style={{ letterSpacing: '0.12em', fontWeight: 800, color: 'var(--text-3)' }}>{c}</div>
                      <div className="t-num" style={{ fontWeight: 800, fontSize: 17, color: 'var(--text)', marginTop: 2, letterSpacing: '-0.015em' }}>
                        {s.prices[c]}<span style={{ fontSize: 10, color: 'var(--text-2)', marginInlineStart: 2 }}>DH</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <Btn variant="soft" block style={{ marginTop: 12 }} onClick={openBooking}>
                {t.bookCta}
              </Btn>
            </div>
          );
        })}
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

function ProfileScreen({ t, lang, setLang, theme, setTheme, variant, setVariant, profile, staffContact, onToast, onLogout }) {
  const [confirmingAllOut, setConfirmingAllOut] = useS_h(false);
  const [confirmingDelete, setConfirmingDelete] = useS_h(false);
  const [logoutBusy, setLogoutBusy] = useS_h(null);
  const [deleteBusy, setDeleteBusy] = useS_h(false);
  const [deleteError, setDeleteError] = useS_h('');
  const impactStats = _readImpactStats();
  const profileLitersCount = useCountUp(impactStats.litersSaved || 0, 1200, !!impactStats.litersSaved);

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
          {profile.name && (
            <button className="icon-btn"><Icons.Edit size={18}/></button>
          )}
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
          <ProfileRow icon={<Icons.CarSide size={18}/>} label={t.myVehicles} value="2 véhicules" />
          <ProfileRow icon={<Icons.Pin size={18}/>} label={t.addresses} value="3" />
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
            right={<ProfileSwitch on={true} />} />
        </ProfileSection>

        <ProfileSection>
          <ProfileRow icon={<Icons.Message size={18}/>} label={t.helpCenter} />
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
          ewash · {t.appVersion} 1.0.0 (Casablanca)
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
