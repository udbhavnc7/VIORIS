import { Link } from 'react-router-dom';
import { profile, socialLinks } from '../data/profile';
import { Icon } from './Icon';

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="site-footer__main">
        <div className="site-footer__brand">
          <Link className="brand" to="/#home">
            <span className="brand__mark">{profile.initials}</span>
            <span className="brand__name">
              <strong>{profile.name}</strong>
              <small>{profile.shortRole}</small>
            </span>
          </Link>
          <p>{profile.intro}</p>
          <div className="social-row">
            {socialLinks.map((social) => (
              <a
                key={social.label}
                href={social.href}
                target={social.href.startsWith('http') ? '_blank' : undefined}
                rel={social.href.startsWith('http') ? 'noreferrer' : undefined}
                aria-label={social.label}
              >
                <Icon name={social.icon} size={18} />
              </a>
            ))}
          </div>
        </div>
        <div className="site-footer__links">
          <div>
            <p className="footer-group-label">General</p>
            <Link to="/#home">Home</Link>
            <Link to="/About">About</Link>
            <Link to="/Projects">Projects</Link>
          </div>
          <div>
            <p className="footer-group-label">Specifics</p>
            <Link to="/#skills">Skills</Link>
            <Link to="/#journey">Journey</Link>
            <Link to="/#achievements">Achievements</Link>
          </div>
          <div>
            <p className="footer-group-label">More</p>
            <Link to="/#contact">Contact</Link>
            <a href={profile.github} target="_blank" rel="noreferrer">GitHub</a>
            <a href={profile.linkedin} target="_blank" rel="noreferrer">LinkedIn</a>
          </div>
        </div>
      </div>
      <div className="site-footer__bottom">
        <p>© {new Date().getFullYear()} {profile.name}. All rights reserved.</p>
        <a href={profile.resume} download>
          <Icon name="download" size={14} /> Resume
        </a>
      </div>
    </footer>
  );
}
