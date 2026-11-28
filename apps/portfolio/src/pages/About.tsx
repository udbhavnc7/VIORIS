import { useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { ContributionHeatmap } from '../components/ContributionHeatmap';
import { Icon } from '../components/Icon';
import { OrbitalPortrait } from '../components/OrbitalPortrait';
import { Reveal } from '../components/Reveal';
import { SectionHeading } from '../components/SectionHeading';
import { profile } from '../data/profile';
import { experienceGroups } from '../data/resume';
import { useGitHubProfile } from '../hooks/useGitHubProfile';

const stackCards = [
  { label: 'Developer', title: 'AI-powered applications', detail: 'Full-stack · Mobile · Data-driven', icon: 'code' as const },
  { label: 'Hackathon', title: 'REVA RIFT Winner', detail: 'VORTEX · AI escrow on Algorand', icon: 'award' as const },
  { label: 'Builder', title: 'Six selected projects', detail: 'Health · Safety · Finance · AI', icon: 'briefcase' as const },
  { label: 'Student', title: 'B.E. CSE (Data Science)', detail: 'St Joseph Engineering College', icon: 'book' as const },
];

function AboutStack() {
  const [active, setActive] = useState(0);
  const card = stackCards[active];

  return (
    <div className="about-stack-wrap">
      <OrbitalPortrait compact />
      <button
        type="button"
        className="about-stack"
        onClick={() => setActive((current) => (current + 1) % stackCards.length)}
        aria-label={`Show next about card. Current: ${card.label}. ${card.title}. ${card.detail}`}
      >
        {stackCards.map((item, index) => (
          <span
            key={item.label}
            className={`about-stack__card ${index === active ? 'is-active' : ''}`}
            style={{ '--stack-index': index } as CSSProperties}
            aria-hidden={index !== active}
          >
            <span className="about-stack__label">{item.label}</span>
            <strong>{item.title}</strong>
            <small>{item.detail}</small>
          </span>
        ))}
        <span className="about-stack__counter">{active + 1}/{stackCards.length} Click →</span>
      </button>
    </div>
  );
}

export function About() {
  const stats = useGitHubProfile();

  return (
    <>
      <section className="about-hero">
        <div className="about-hero__ambient" aria-hidden="true" />
        <div className="container about-hero__grid">
          <Reveal className="about-hero__content">
            <p className="eyebrow"><span aria-hidden="true" /> More about me</p>
            <h1>I’m Udbhav, a<br /><em>developer</em></h1>
            <p>{profile.about}</p>
            <p>{profile.aboutSecondary}</p>
            <div className="social-row">
              <a href={profile.linkedin} target="_blank" rel="noreferrer" aria-label="LinkedIn"><Icon name="linkedin" size={18} /></a>
              <a href={profile.github} target="_blank" rel="noreferrer" aria-label="GitHub"><Icon name="github" size={18} /></a>
              <a href={`mailto:${profile.email}`} aria-label="Email"><Icon name="mail" size={18} /></a>
            </div>
          </Reveal>
          <Reveal className="about-hero__visual" delay={100}>
            <AboutStack />
          </Reveal>
        </div>
      </section>

      <section className="section about-experience">
        <div className="container">
          <Reveal>
            <SectionHeading eyebrow="The experience" title={<>Experience that <em>brings ideas to life</em></>} />
          </Reveal>
          <div className="about-experience__list">
            {experienceGroups.map((group, index) => (
              <Reveal as="article" className="about-experience__item" key={group.label} delay={index * 70}>
                <div className="about-experience__marker"><span>{String(index + 1).padStart(2, '0')}</span></div>
                <div className="about-experience__body">
                  <p className="eyebrow">{group.label}</p>
                  <h3>{group.title}</h3>
                  <p>{group.description}</p>
                  <div className="tag-row">{group.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section className="section github-section">
        <div className="container github-section__grid">
          <Reveal className="github-section__copy">
            <SectionHeading
              eyebrow="Developer insights"
              title={<>Consistency,<br /><em>visualized</em></>}
              description="A live view of my public GitHub contribution activity. Select the grid to explore it in 3D."
            />
            <div className="github-stats">
              <div><strong>{stats.followers}</strong><span>Followers</span></div>
              <div><strong>{stats.stars}</strong><span>Total Stars</span></div>
              <div><strong>{stats.repos}</strong><span>Public Repos</span></div>
              <div><strong>{stats.forks}</strong><span>Total Forks</span></div>
            </div>
          </Reveal>
          <Reveal className="github-section__widget" delay={100}>
            <ContributionHeatmap />
          </Reveal>
        </div>
      </section>

      <section className="about-cta">
        <div className="container about-cta__inner">
          <Reveal>
            <p className="eyebrow"><span aria-hidden="true" /> From concept to creation</p>
            <h2>LET’S MAKE<br /><em>IT HAPPEN!</em></h2>
            <p>I’m open to internships, collaborations, and conversations about building useful software.</p>
            <Link className="button" to="/#contact">Get in touch <Icon name="arrow-up-right" size={17} /></Link>
          </Reveal>
        </div>
      </section>
    </>
  );
}
