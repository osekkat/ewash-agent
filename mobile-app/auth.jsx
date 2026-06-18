/* eslint-disable */
// ewash — onboarding (splash, language)

const { useState: useS_auth, useEffect: useE_auth } = React;

// ─────────────────────────────────────────────────────────────
// Splash — auto-advances after a beat
// ─────────────────────────────────────────────────────────────
function SplashScreen({ onDone, t }) {
  useE_auth(() => {
    const id = setTimeout(onDone, 1400);
    return () => clearTimeout(id);
  }, []);
  return (
    <div className="col" style={{
      flex: 1,
      background: 'var(--hero-grad)',
      color: '#fff',
      alignItems: 'center', justifyContent: 'center',
      padding: 32, position: 'relative', overflow: 'hidden',
    }}>
      {/* decorative ripples */}
      <div style={{
        position: 'absolute', inset: 0, opacity: 0.18,
        background: `
          radial-gradient(circle at 80% 20%, rgba(132,196,43,0.6) 0%, transparent 40%),
          radial-gradient(circle at 15% 85%, rgba(111,224,197,0.5) 0%, transparent 40%)
        `,
      }} />
      <div className="col gap-16 anim-fade" style={{ alignItems: 'center', zIndex: 1 }}>
        <div style={{
          width: 96, height: 96,
          borderRadius: 28,
          background: 'rgba(255,255,255,0.10)',
          backdropFilter: 'blur(20px)',
          border: '1px solid rgba(255,255,255,0.18)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <img src="assets/ewash-logo.png" width={72} height={72} alt="Ewash"
            style={{ display: 'block', filter: 'brightness(1.05)' }}/>
        </div>
        <div className="t-display" style={{ color: '#fff', fontSize: 36 }}>Ewash</div>
        <div style={{ opacity: 0.75, fontSize: 14, fontWeight: 500, textAlign: 'center', maxWidth: 240 }}>
          {t.tagline}
        </div>
      </div>
      <div style={{ position: 'absolute', bottom: 38, opacity: 0.7, display: 'flex', gap: 8 }}>
        <span style={{ width: 6, height: 6, borderRadius: 6, background: '#fff', animation: 'pulse 1s infinite' }}/>
        <span style={{ width: 6, height: 6, borderRadius: 6, background: '#fff', animation: 'pulse 1s infinite 0.2s' }}/>
        <span style={{ width: 6, height: 6, borderRadius: 6, background: '#fff', animation: 'pulse 1s infinite 0.4s' }}/>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Language selection — first-run only
// ─────────────────────────────────────────────────────────────
function LangScreen({ onDone, t, lang, setLang }) {
  const [pending, setPending] = useS_auth(lang);
  return (
    <div className="col" style={{ flex: 1 }}>
      <div className="col gap-16 px-20" style={{ paddingTop: 32 }}>
        <img src="assets/ewash_logo_only.png" width={44} height={44} alt="Ewash"
          style={{ display: 'block' }}/>
        <div className="col gap-6">
          <div className="t-h1">{t.chooseLang}</div>
          <div className="t-muted">Select your language · اختر لغتك</div>
        </div>
      </div>
      <div className="col gap-12 px-20 mt-24 flex-1">
        {[
          { code: 'fr', label: 'Français', sub: 'Default' },
          { code: 'ar', label: 'العربية', sub: 'RTL', rtl: true },
        ].map((opt) => (
          <button key={opt.code}
            onClick={() => setPending(opt.code)}
            className="card"
            style={{
              borderColor: pending === opt.code ? 'var(--primary)' : 'var(--border)',
              background: pending === opt.code ? 'var(--primary-soft)' : 'var(--surface)',
              padding: '18px 18px',
              display: 'flex', alignItems: 'center', gap: 12,
              direction: opt.rtl ? 'rtl' : 'ltr',
            }}>
            <div style={{
              width: 44, height: 44, borderRadius: 14,
              background: pending === opt.code ? 'var(--primary)' : 'var(--surface-2)',
              color: pending === opt.code ? 'var(--primary-text)' : 'var(--text)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontFamily: opt.rtl ? 'var(--font-ar)' : 'var(--font-display)',
              fontWeight: 800, fontSize: 18,
            }}>
              {opt.rtl ? 'ع' : 'Aa'}
            </div>
            <div className="col gap-4 flex-1" style={{ textAlign: 'inherit' }}>
              <div style={{ fontWeight: 700, fontSize: 16, fontFamily: opt.rtl ? 'var(--font-ar)' : 'inherit' }}>{opt.label}</div>
              <div className="t-tiny">{opt.sub}</div>
            </div>
            <div style={{
              width: 22, height: 22, borderRadius: 999,
              border: `2px solid ${pending === opt.code ? 'var(--primary)' : 'var(--border-strong)'}`,
              background: pending === opt.code ? 'var(--primary)' : 'transparent',
              color: 'var(--primary-text)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              {pending === opt.code && <Icons.Check size={14} stroke={3}/>}
            </div>
          </button>
        ))}
      </div>
      <CtaDock>
        <Btn block lg onClick={() => { setLang(pending); onDone(); }}>{t.continue}</Btn>
      </CtaDock>
    </div>
  );
}

Object.assign(window, { SplashScreen, LangScreen });
