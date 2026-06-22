/* eslint-disable */
// ewash — shared UI components

const { useState, useEffect, useRef, useMemo, useCallback } = React;
const EWASH_OFFICIAL_WHATSAPP = '+212' + '611204502';
window.EWASH_OFFICIAL_WHATSAPP = EWASH_OFFICIAL_WHATSAPP;

// ─────────────────────────────────────────────────────────────
// Logo block (uses uploaded logo image; falls back to wordmark)
// ─────────────────────────────────────────────────────────────
function Wordmark({ size = 24, color }) {
  return (
    <span className="wordmark" style={{ fontSize: size, color: color || 'var(--primary)' }}>
      Ewash
    </span>
  );
}

function LogoStack({ variant }) {
  // Eco splash kept the full ewash-logo.png (mark + wordmark) for a big
  // brand statement; premium previously fell back to the SVG glyph and
  // now uses ewash_logo_only.png — the same mark-only asset already on
  // the appbar and language picker, sized larger to suit a splash slot.
  const isEco = variant === 'eco';
  return (
    <div className="col gap-8" style={{ alignItems: 'center' }}>
      <img
        src={isEco ? 'assets/ewash-logo.png' : 'assets/ewash_logo_only.png'}
        width={isEco ? 92 : 76}
        height={isEco ? 92 : 76}
        alt="Ewash"
        style={{ display: 'block' }}
      />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Top app bar
// ─────────────────────────────────────────────────────────────
function _helpLabel(t) {
  return (t && t.topBar && t.topBar.help) || (t && t.helpCenter) || 'Aide';
}

function _helpMessage(t, currentScreen) {
  const template = (t && t.help && t.help.deepLinkMessage) ||
    "Bonjour Ewash, j'ai besoin d'aide depuis l'application (écran: {screen}).";
  return template.replace('{screen}', currentScreen || 'app');
}

function _helpPhoneDigits(_staffContact) {
  return String(EWASH_OFFICIAL_WHATSAPP || '').replace(/[^0-9]/g, '');
}

function _canOpenHelp(staffContact) {
  return !!_helpPhoneDigits(staffContact);
}

function _openHelp(staffContact, currentScreen, t) {
  if (!_canOpenHelp(staffContact)) return;
  if (window.EwashLog) window.EwashLog.info('help.opened', { from_screen: currentScreen || 'app', phone: 'official' });
  const phone = _helpPhoneDigits(staffContact);
  const url = 'https://wa.me/' + phone + '?text=' + encodeURIComponent(_helpMessage(t, currentScreen));
  window.open(url, '_blank', 'noopener,noreferrer');
}

function HelpButton({ t, staffContact, currentScreen }) {
  if (!_canOpenHelp(staffContact)) return null;
  const label = _helpLabel(t);
  return (
    <button
      className="icon-btn help-btn"
      onClick={() => _openHelp(staffContact, currentScreen, t)}
      aria-label={label}
      title={label}
      type="button"
    >
      <Icons.Message size={18} />
    </button>
  );
}

function TopBar({ title, onBack, right, subtitle, large = false, t, staffContact, currentScreen }) {
  return (
    <div className="appbar">
      <div className="row gap-8" style={{ minWidth: 40 }}>
        {onBack && (
          <button className="icon-btn" onClick={onBack} aria-label="back">
            <Icons.ChevronLeft size={22} />
          </button>
        )}
      </div>
      <div className="col" style={{ alignItems: 'center', flex: 1, minWidth: 0 }}>
        {title && <div className="appbar-title" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</div>}
        {subtitle && <div className="t-tiny" style={{ marginTop: 2 }}>{subtitle}</div>}
      </div>
      <div className="row gap-4" style={{ minWidth: 40, justifyContent: 'flex-end' }}>
        <HelpButton t={t} staffContact={staffContact} currentScreen={currentScreen} />
        {right}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Bottom Nav
// ─────────────────────────────────────────────────────────────
function BottomNav({ screen, setScreen, t }) {
  const ICON_BY_KEY = {
    home: Icons.Home,
    bookings: Icons.Calendar,
    services: Icons.Sparkle,
    profile: Icons.User,
  };
  const items = [
    { key: 'home', label: t.home },
    { key: 'bookings', label: t.bookings },
    { key: 'services', label: t.services },
    { key: 'profile', label: t.profile },
  ];
  return (
    <div className="bottom-nav">
      {items.map((item) => {
        const IconComp = ICON_BY_KEY[item.key];
        const active = screen === item.key;
        return (
          <button key={item.key} className={`navitem ${active ? 'active' : ''}`}
            onClick={() => setScreen(item.key)}>
            <div className="pill">
              <IconComp size={22} stroke={active ? 2.4 : 2} />
            </div>
            <span>{item.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Stepper progress
// ─────────────────────────────────────────────────────────────
function Stepper({ current, total }) {
  return (
    <div className="stepbar">
      {Array.from({ length: total }).map((_, i) => (
        <div key={i} className={`seg ${i < current ? 'done' : i === current ? 'active' : ''}`} />
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Button helper
// ─────────────────────────────────────────────────────────────
function Btn({ variant = 'primary', block, lg, children, icon, ...rest }) {
  const cls = `btn btn-${variant} ${block ? 'btn-block' : ''} ${lg ? 'btn-lg' : ''}`;
  return (
    <button className={cls} {...rest}>
      {icon}
      <span>{children}</span>
    </button>
  );
}

// ─────────────────────────────────────────────────────────────
// Sticky CTA dock at bottom
// ─────────────────────────────────────────────────────────────
function CtaDock({ children, hint, className = '' }) {
  const cls = ['cta-dock', className].filter(Boolean).join(' ');
  return (
    <div className={cls}>
      {hint && <div className="t-tiny text-center mb-8">{hint}</div>}
      {children}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Field
// ─────────────────────────────────────────────────────────────
function Field({ label, hint, children }) {
  return (
    <label className="col gap-6" style={{ display: 'block' }}>
      <span className="t-muted" style={{ fontWeight: 600, fontSize: 12.5, letterSpacing: 0.02 }}>{label}</span>
      {children}
      {hint && <span className="t-tiny">{hint}</span>}
    </label>
  );
}

// ─────────────────────────────────────────────────────────────
// Selectable card row (radio behavior)
// ─────────────────────────────────────────────────────────────
function SelectCard({ selected, onClick, children, icon, badge }) {
  return (
    <button onClick={onClick}
      className={`svc-card ${selected ? 'selected' : ''}`}
      style={{ textAlign: 'inherit', width: '100%', position: 'relative' }}>
      {icon && (
        <div className="thumb" style={{
          background: selected ? 'var(--primary)' : 'var(--surface-2)',
          color: selected ? 'var(--primary-text)' : 'var(--text)',
        }}>
          {icon}
        </div>
      )}
      <div className="flex-1 col gap-4" style={{ minWidth: 0, justifyContent: 'center' }}>
        {children}
      </div>
      {badge && <div style={{ position: 'absolute', top: 10, insetInlineEnd: 10 }}>{badge}</div>}
      <div className="center" style={{ width: 24, alignSelf: 'center' }}>
        <div style={{
          width: 22, height: 22, borderRadius: 999,
          border: `2px solid ${selected ? 'var(--primary)' : 'var(--border-strong)'}`,
          background: selected ? 'var(--primary)' : 'transparent',
          color: 'var(--primary-text)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          transition: 'border-color 0.18s var(--ease-soft), background 0.18s var(--ease-soft), transform 0.22s var(--ease-spring)',
          transform: selected ? 'scale(1)' : 'scale(0.92)',
          boxShadow: selected ? '0 4px 12px -4px color-mix(in srgb, var(--primary) 50%, transparent)' : 'none',
        }}>
          {selected && <Icons.Check size={14} stroke={3} />}
        </div>
      </div>
    </button>
  );
}

// ─────────────────────────────────────────────────────────────
// Sheet (bottom sheet modal)
// ─────────────────────────────────────────────────────────────
function Sheet({ open, onClose, children }) {
  if (!open) return null;
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-grabber" />
        {children}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Toast (auto-dismiss)
// ─────────────────────────────────────────────────────────────
function Toast({ message, onDone, icon }) {
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(onDone, 2200);
    return () => clearTimeout(t);
  }, [message]);
  if (!message) return null;
  return (
    <div className="toast">
      {icon || <Icons.CheckCircle size={18} />}
      <span>{message}</span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Status bar + nav bar (custom slim, no app bar)
// ─────────────────────────────────────────────────────────────
function PhoneStatusBar({ theme }) {
  const c = theme === 'dark' ? '#fff' : '#0E2A2A';
  return (
    <div style={{
      height: 32, display: 'flex', alignItems: 'center',
      justifyContent: 'space-between', padding: '0 18px',
      position: 'relative', flexShrink: 0,
      background: 'var(--bg)',
      fontFamily: 'var(--font-display)',
      fontSize: 13, fontWeight: 700, color: c,
    }}>
      <span>9:30</span>
      <div style={{
        position: 'absolute', left: '50%', top: 6,
        transform: 'translateX(-50%)',
        width: 22, height: 22, borderRadius: 99,
        background: '#0a0a0a',
      }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <svg width="14" height="10" viewBox="0 0 14 10">
          <rect x="0.5" y="6" width="2" height="3" rx="0.5" fill={c}/>
          <rect x="4" y="4" width="2" height="5" rx="0.5" fill={c}/>
          <rect x="7.5" y="2" width="2" height="7" rx="0.5" fill={c}/>
          <rect x="11" y="0" width="2" height="9" rx="0.5" fill={c}/>
        </svg>
        <svg width="14" height="10" viewBox="0 0 14 10">
          <path d="M7 8.5L1 3.5a8 8 0 0112 0L7 8.5z" fill={c}/>
        </svg>
        <svg width="22" height="11" viewBox="0 0 22 11">
          <rect x="0.5" y="0.5" width="18" height="10" rx="2.5" fill="none" stroke={c} strokeWidth="1"/>
          <rect x="2.5" y="2.5" width="13" height="6" rx="1.2" fill={c}/>
          <rect x="19.5" y="3.5" width="1.5" height="4" rx="0.5" fill={c}/>
        </svg>
      </div>
    </div>
  );
}

function PhoneNavBar({ theme }) {
  return (
    <div style={{
      height: 22, display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--surface)', flexShrink: 0,
    }}>
      <div style={{
        width: 108, height: 4, borderRadius: 2,
        background: theme === 'dark' ? '#fff' : '#0E2A2A',
        opacity: 0.5,
      }} />
    </div>
  );
}

// Custom Android device — matches our brand colors instead of M3 defaults
function EwashFrame({ children, theme }) {
  return (
    <div style={{
      width: 392, height: 820,
      borderRadius: 44,
      background: theme === 'dark' ? '#0a0a0a' : '#1a1a1a',
      padding: 9,
      boxShadow: '0 30px 80px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.04)',
    }}>
      <div style={{
        width: '100%', height: '100%',
        borderRadius: 36,
        overflow: 'hidden',
        display: 'flex', flexDirection: 'column',
        background: 'var(--bg)',
        position: 'relative',
      }}>
        <PhoneStatusBar theme={theme} />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, position: 'relative' }}>
          {children}
        </div>
        <PhoneNavBar theme={theme} />
      </div>
    </div>
  );
}

Object.assign(window, {
  Wordmark, LogoStack, TopBar, BottomNav, Stepper, Btn,
  CtaDock, Field, SelectCard, Sheet, Toast, EwashFrame,
  HelpButton,
});
