/* eslint-disable */
// ewash — main app

const { useState: useS_a, useEffect: useE_a, useMemo: useM_a } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "variant": "eco",
  "theme": "light",
  "lang": "fr"
}/*EDITMODE-END*/;

function _copyDebugText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  const el = document.createElement('textarea');
  el.value = text;
  el.setAttribute('readonly', '');
  el.style.position = 'fixed';
  el.style.inset = '0 auto auto -9999px';
  document.body.appendChild(el);
  el.select();
  try {
    document.execCommand('copy');
  } finally {
    document.body.removeChild(el);
  }
  return Promise.resolve();
}

function DebugOverlay() {
  const logger = window.EwashLog;
  const [entries, setEntries] = useS_a(() => logger ? logger.snapshot() : []);
  const [collapsed, setCollapsed] = useS_a(true);
  const [copied, setCopied] = useS_a(false);

  useE_a(() => {
    if (!logger) return undefined;
    setEntries(logger.snapshot());
    const onLog = (event) => {
      setEntries((prev) => prev.concat(event.detail).slice(-100));
    };
    window.addEventListener('ewashlog', onLog);
    return () => window.removeEventListener('ewashlog', onLog);
  }, [logger]);

  if (!logger || !logger.debugMode) return null;

  const copyLogs = (event) => {
    event.stopPropagation();
    _copyDebugText(JSON.stringify(logger.snapshot(), null, 2)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    }).catch(() => {
      if (logger) logger.warn('lifecycle.debug_copy', { error_code: 'clipboard_failed' });
    });
  };

  return (
    <div className="debug-overlay" data-collapsed={collapsed ? 'true' : 'false'}>
      <header onClick={() => setCollapsed(!collapsed)}>
        <span>EwashLog · {entries.length} · {logger.sessionId}</span>
        <button type="button" onClick={copyLogs}>{copied ? 'Copied' : 'Copy'}</button>
      </header>
      {!collapsed && (
        <div className="entries">
          {entries.slice(-40).map((entry, idx) => {
            const payload = Object.assign({}, entry);
            delete payload.t;
            delete payload.scope;
            delete payload.level;
            delete payload.session;
            return (
              <div key={idx} className={`entry level-${entry.level}`}>
                <small>{entry.t.split('T')[1].slice(0, 8)}</small>
                <code>{entry.scope}</code>
                <pre>{JSON.stringify(payload)}</pre>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const EWASH_LANG_STORAGE_KEY = 'ewash.lang';

function _readStoredLang() {
  try {
    if (typeof localStorage === 'undefined') return '';
    const stored = localStorage.getItem(EWASH_LANG_STORAGE_KEY) || '';
    return ['fr', 'ar'].includes(stored) ? stored : '';
  } catch (_) {
    return '';
  }
}

function _writeStoredLang(lang) {
  try {
    if (typeof localStorage !== 'undefined' && ['fr', 'ar'].includes(lang)) {
      localStorage.setItem(EWASH_LANG_STORAGE_KEY, lang);
    }
  } catch (_) {}
}

function _readStoredProfile() {
  try {
    const nameKey = (window.EwashAPI && window.EwashAPI._NAME_KEY) || 'ewash.name';
    const phoneKey = (window.EwashAPI && window.EwashAPI._PHONE_KEY) || 'ewash.phone';
    return {
      name: (typeof localStorage !== 'undefined' && localStorage.getItem(nameKey)) || '',
      phone: (typeof localStorage !== 'undefined' && localStorage.getItem(phoneKey)) || '',
    };
  } catch (_) {
    return { name: '', phone: '' };
  }
}

function _initialPhase() {
  const profile = _readStoredProfile();
  return (_readStoredLang() || profile.name || profile.phone) ? 'app' : 'lang';
}

// ─────────────────────────────────────────────────────────────
// MAIN APP
// ─────────────────────────────────────────────────────────────
function App() {
  const [tweaks, setTweak] = useTweaks(Object.assign({}, TWEAK_DEFAULTS, { lang: _readStoredLang() || TWEAK_DEFAULTS.lang }));
  const { variant, theme, lang } = tweaks;

  const t = window.I18N[lang] || window.I18N.fr;
  const dir = lang === 'ar' ? 'rtl' : 'ltr';

  // App-level navigation state
  const [phase, setPhase] = useS_a(_initialPhase);
  // phase: splash → lang → app. Language is requested once, then editable from Profile.
  const [tab, setTab] = useS_a('home'); // home, bookings, services, profile
  const [modal, setModal] = useS_a(null); // 'booking' | 'support' | null
  const [bookingPrefill, setBookingPrefill] = useS_a(null);
  const [toast, setToast] = useS_a(null);
  // App-level staff_contact, sourced from the bootstrap endpoint once at
  // startup. Used by BookingsScreen's detail modal "Contacter le support"
  // CTA — booking.jsx fetches its own bootstrap inside the flow. Keep the
  // official Ewash WhatsApp as a guaranteed fallback so help works immediately.
  const [staffContact, setStaffContact] = useS_a(() => ({
    available: true,
    whatsapp_phone: window.EWASH_OFFICIAL_WHATSAPP || ('+212' + '611204502'),
  }));

  const log = window.EwashLog;
  const setPhaseLogged = (next) => {
    if (log) log.info('lifecycle.phase', { from_phase: phase, to_phase: next });
    setPhase(next);
  };
  const setTabLogged = (next) => {
    if (log && next !== tab) log.info('lifecycle.tab', { from_tab: tab, to_tab: next });
    setTab(next);
  };
  const setLangStored = (next) => {
    _writeStoredLang(next);
    setTweak('lang', next);
  };
  const openBookingModal = (prefill) => {
    setBookingPrefill(prefill || null);
    setModal('booking');
  };
  const closeBookingModal = () => {
    setBookingPrefill(null);
    setModal(null);
  };
  const openSupportWhatsApp = () => {
    if (typeof _openTeamChat === 'function') _openTeamChat(t, staffContact);
    else setModal('support');
  };

  // Anonymous-until-first-booking: name/phone are empty for new users and get
  // populated by api.submitBooking after a successful POST /api/v1/bookings.
  // HomeScreen and ProfileScreen render an empty-state when these are blank.
  const [profile, setProfile] = useS_a(_readStoredProfile);
  const refreshProfile = () => setProfile(_readStoredProfile());

  useE_a(() => {
    document.documentElement.setAttribute('data-variant', variant);
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.setAttribute('dir', dir);
  }, [variant, theme, dir]);

  // Render full-viewport (no desktop phone-frame stage) when:
  //   - PWA is installed (display-mode: standalone)
  //   - iOS Safari standalone
  //   - URL has ?pwa=1 (manual preview)
  //   - Viewport is phone-sized → real mobile visitors see the real app.
  // The stage is only useful on wide screens (design preview / sharing
  // the link to a desktop browser to show off the mockup).
  const isStandalone = useM_a(() => {
    if (typeof window === 'undefined') return false;
    try {
      if (window.matchMedia('(display-mode: standalone)').matches) return true;
    } catch (e) { /* ignore */ }
    if (window.navigator && window.navigator.standalone) return true;
    if (new URLSearchParams(window.location.search).has('pwa')) return true;
    if (typeof window.matchMedia === 'function' &&
        window.matchMedia('(max-width: 600px)').matches) return true;
    return false;
  }, []);

  useE_a(() => {
    if (isStandalone) document.documentElement.setAttribute('data-pwa-standalone', '');
    else document.documentElement.removeAttribute('data-pwa-standalone');
  }, [isStandalone]);

  useE_a(() => {
    // Keep every customer help/support CTA on the official Ewash WhatsApp Business number.
    // Backend bootstrap staff_contact may point to an internal/test number, which must
    // never leak into the customer PWA.
    setStaffContact({
      available: true,
      whatsapp_phone: window.EWASH_OFFICIAL_WHATSAPP || ('+212' + '611204502'),
    });
  }, []);

  // ───── Phase rendering
  let phaseContent = null;
  if (phase === 'splash') {
    phaseContent = <SplashScreen t={t} onDone={() => setPhaseLogged('lang')} />;
  } else if (phase === 'lang') {
    phaseContent = <LangScreen t={t} lang={lang}
      setLang={setLangStored}
      onDone={() => { _writeStoredLang(lang); setPhaseLogged('app'); }}/>
  } else if (phase === 'app') {
    phaseContent = (
      <>
        {!modal && tab === 'home' && (
          <HomeScreen t={t} lang={lang} variant={variant} theme={theme}
            profile={profile}
            staffContact={staffContact}
            openBooking={openBookingModal}
            gotoSupport={openSupportWhatsApp}
            gotoBookings={() => setTabLogged('bookings')}
            gotoTariffs={() => setTabLogged('services')}/>
        )}
        {!modal && tab === 'bookings' && (
          <BookingsScreen t={t} lang={lang} openBooking={openBookingModal} theme={theme}
            staffContact={staffContact}/>
        )}
        {!modal && tab === 'services' && (
          <ServicesScreen t={t} lang={lang} openBooking={openBookingModal} theme={theme}
            staffContact={staffContact}/>
        )}
        {!modal && tab === 'profile' && (
          <ProfileScreen t={t} lang={lang}
            setLang={setLangStored}
            theme={theme}
            setTheme={(th) => setTweak('theme', th)}
            variant={variant}
            setVariant={(v) => setTweak('variant', v)}
            profile={profile}
            staffContact={staffContact}
            onOpenSupport={openSupportWhatsApp}
            onProfileChanged={refreshProfile}
            onToast={setToast}
            onLogout={() => { refreshProfile(); setPhaseLogged('lang'); setTabLogged('home'); }}/>
        )}
        {modal === 'booking' && (
          <BookingFlow t={t} lang={lang} theme={theme} variant={variant}
            profile={profile}
            prefill={bookingPrefill}
            staffContact={staffContact}
            onClose={closeBookingModal}
            onComplete={() => { refreshProfile(); closeBookingModal(); setTabLogged('home'); setToast(t.bookingConfirmed); }}/>
        )}
        {modal === 'support' && (
          <SupportScreen t={t} theme={theme} staffContact={staffContact}
            onBack={() => setModal(null)}/>
        )}
        {!modal && (
          <BottomNav t={t} screen={tab} setScreen={setTabLogged}/>
        )}
      </>
    );
  }

  return (
    <>
      {isStandalone ? (
        <div className="app-root" dir={dir} style={{ direction: dir }}>
          {phaseContent}
          <Toast message={toast} onDone={() => setToast(null)} />
        </div>
      ) : (
        <div className="stage" dir={dir}>
          <div className="stage-inner">
            <span className="stage-label">Ewash · Android · {variant} · {lang.toUpperCase()}</span>
            <EwashFrame theme={theme}>
              <div className="app-root" dir={dir} style={{ direction: dir }}>
                {phaseContent}
                <Toast message={toast} onDone={() => setToast(null)} />
              </div>
            </EwashFrame>
          </div>
        </div>
      )}

      <TweaksPanel>
        <TweakSection label="Direction" />
        <TweakRadio label="Style" value={variant}
          onChange={(v) => setTweak('variant', v)}
          options={[
            { label: 'Eco', value: 'eco' },
            { label: 'Premium', value: 'premium' },
          ]}/>
        <TweakRadio label="Mode" value={theme}
          onChange={(v) => setTweak('theme', v)}
          options={[
            { label: 'Light', value: 'light' },
            { label: 'Dark', value: 'dark' },
          ]}/>
        <TweakSection label="Localization" />
        <TweakRadio label="Language" value={lang}
          onChange={setLangStored}
          options={[
            { label: 'FR', value: 'fr' },
            { label: 'AR', value: 'ar' },
          ]}/>
        <TweakSection label="Flow" />
        <TweakButton label="Replay onboarding"
          onClick={() => { setPhaseLogged('splash'); setTabLogged('home'); setModal(null); }}/>
        <TweakButton label="Skip to app"
          onClick={() => { setPhaseLogged('app'); setModal(null); }}/>
        <TweakButton label="Start a booking"
          onClick={() => { setPhaseLogged('app'); setModal('booking'); }}/>
      </TweaksPanel>
      <DebugOverlay />
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
