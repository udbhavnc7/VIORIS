import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { profile } from '../data/profile';
import { Icon } from './Icon';

const moreItems = [
  { label: 'Skills', to: '/#skills' },
  { label: 'Journey', to: '/#journey' },
  { label: 'Achievements', to: '/#achievements' },
  { label: 'Code Chronicles', to: '/codechronicles' },
];

const navItems = [
  { label: 'Home', to: '/#home' },
  { label: 'About', to: '/About' },
  { label: 'Projects', to: '/Projects' },
  { label: 'Contact', to: '/#contact' },
];

export function Header() {
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  const menuToggleRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setMobileOpen(false);
    setMoreOpen(false);
  }, [location.pathname, location.hash]);

  useEffect(() => {
    document.body.classList.toggle('menu-open', mobileOpen);
    return () => document.body.classList.remove('menu-open');
  }, [mobileOpen]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (moreRef.current && !moreRef.current.contains(event.target as Node)) setMoreOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (moreOpen) {
        setMoreOpen(false);
        moreButtonRef.current?.focus();
      }
      if (mobileOpen) {
        setMobileOpen(false);
        menuToggleRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [moreOpen, mobileOpen]);

  const isActive = (to: string) => {
    if (to.startsWith('/#')) return location.pathname === '/' && location.hash === to.slice(1);
    return location.pathname.toLowerCase() === to.toLowerCase();
  };

  return (
    <header className="site-header">
      <div className="site-header__inner">
        <Link className="brand" to="/#home" aria-label={`${profile.name} home`}>
          <span className="brand__mark">{profile.initials}</span>
          <span className="brand__name">
            <strong>{profile.name}</strong>
            <small>{profile.shortRole}</small>
          </span>
        </Link>

        <nav className="desktop-nav" aria-label="Primary navigation">
          {navItems.slice(0, 3).map((item) => (
            <Link key={item.label} className={isActive(item.to) ? 'is-active' : ''} to={item.to}>
              {item.label}
            </Link>
          ))}
          <div className="nav-more" ref={moreRef}>
            <button
              ref={moreButtonRef}
              type="button"
              className={moreOpen ? 'is-active' : ''}
              aria-expanded={moreOpen}
              aria-controls="more-navigation"
              onClick={() => setMoreOpen((open) => !open)}
            >
              More <Icon name="chevron-down" size={13} />
            </button>
            {moreOpen ? (
              <div id="more-navigation" className="nav-more__menu">
                {moreItems.map((item) => (
                  <Link key={item.label} to={item.to} onClick={() => setMoreOpen(false)}>
                    {item.label}
                    <Icon name="arrow-up-right" size={14} />
                  </Link>
                ))}
              </div>
            ) : null}
          </div>
          <Link className={isActive('/#contact') ? 'is-active' : ''} to="/#contact">
            Contact
          </Link>
        </nav>

        <div className="site-header__actions">
          <a
            className="button button--small hire-button"
            href={`mailto:${profile.email}?subject=${encodeURIComponent("Let's Work Together")}&body=${encodeURIComponent(
              `Hi ${profile.firstName},\n\nI came across your portfolio and would like to discuss a potential opportunity.`,
            )}`}
          >
            Hire Me
          </a>
          <button
            ref={menuToggleRef}
            type="button"
            className="menu-toggle"
            aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={mobileOpen}
            aria-controls="mobile-navigation"
            onClick={() => setMobileOpen((open) => !open)}
          >
            <Icon name={mobileOpen ? 'close' : 'menu'} size={21} />
          </button>
        </div>
      </div>

      {mobileOpen ? (
        <nav id="mobile-navigation" className="mobile-nav" aria-label="Mobile navigation">
          {navItems.map((item) => (
            <Link key={item.label} className={isActive(item.to) ? 'is-active' : ''} to={item.to}>
              {item.label}
              <Icon name="arrow-up-right" size={16} />
            </Link>
          ))}
          <div className="mobile-nav__section">
            <span>More</span>
            {moreItems.map((item) => (
              <Link key={item.label} to={item.to}>
                {item.label}
                <Icon name="arrow-up-right" size={15} />
              </Link>
            ))}
          </div>
          <div className="mobile-nav__socials">
            <a href={profile.github} target="_blank" rel="noreferrer" aria-label="GitHub">
              <Icon name="github" size={19} />
            </a>
            <a href={profile.linkedin} target="_blank" rel="noreferrer" aria-label="LinkedIn">
              <Icon name="linkedin" size={18} />
            </a>
            <a href={`mailto:${profile.email}`} aria-label="Email">
              <Icon name="mail" size={19} />
            </a>
          </div>
        </nav>
      ) : null}
    </header>
  );
}
