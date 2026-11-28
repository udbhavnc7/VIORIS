import { useState } from 'react';
import { ContactForm } from '../components/ContactForm';
import { Icon } from '../components/Icon';
import { IntroDialog } from '../components/IntroDialog';
import { OrbitalPortrait } from '../components/OrbitalPortrait';
import { ProjectCard } from '../components/ProjectCard';
import { Reveal } from '../components/Reveal';
import { SectionHeading } from '../components/SectionHeading';
import { profile, socialLinks } from '../data/profile';
import { projects } from '../data/projects';
import { achievements, certifications, education, skillGroups } from '../data/resume';

const techMarks = [
  ['JS', 'JavaScript'],
  ['TS', 'TypeScript'],
  ['PY', 'Python'],
  ['RE', 'React'],
  ['ND', 'Node.js'],
  ['FL', 'Flutter'],
  ['FB', 'Firebase'],
  ['SU', 'Supabase'],
];

export function Home() {
  const [introOpen, setIntroOpen] = useState(false);

  return (
    <>
      <section id="home" className="hero">
        <div className="hero__ambient" aria-hidden="true" />
        <div className="container hero__grid">
          <div className="hero__content">
            <p className="eyebrow hero__eyebrow"><span aria-hidden="true" /> Welcome to my portfolio</p>
            <h1>
              Hi, I’m <em>Udbhav</em>
              <br />
              Chandragiri
            </h1>
            <p className="hero__script">developer.</p>
            <p className="hero__intro">{profile.intro}</p>
            <div className="hero__actions">
              <a className="button" href="#projects">View Work <Icon name="arrow-up-right" size={17} /></a>
              <a className="button button--ghost" href={profile.resume} download>
                <Icon name="download" size={17} /> View Resume
              </a>
            </div>
            <div className="social-row hero__socials">
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
          <div className="hero__visual">
            <OrbitalPortrait onPlay={() => setIntroOpen(true)} />
          </div>
        </div>
        <a className="hero__scroll" href="#about" aria-label="Scroll to about section">
          <span /> Scroll
        </a>
      </section>

      <section id="about" className="section about-section">
        <div className="container">
          <Reveal>
            <SectionHeading eyebrow="About me" title={<>A little about <em>how I build things</em></>} />
          </Reveal>
          <div className="about-grid">
            <Reveal className="about-visual" delay={80}>
              <div className="about-visual__card">
                <span className="about-visual__corner" aria-hidden="true" />
                <div className="about-visual__monogram">{profile.initials}</div>
                <p>CS · DATA SCIENCE</p>
                <small>{profile.location}</small>
              </div>
              <div className="trust-card">
                <div className="trust-card__icon"><Icon name="sparkles" size={19} /></div>
                <div>
                  <strong>Built on real work</strong>
                  <p>Every project, skill, and milestone here comes from my resume or public repositories.</p>
                </div>
              </div>
            </Reveal>
            <Reveal className="about-content" delay={140}>
              <p>{profile.about}</p>
              <p>{profile.aboutSecondary}</p>
              <div className="about-stats">
                <div><strong>{profile.stats.projects}</strong><span>Selected projects</span></div>
                <div><strong>{profile.stats.hackathonMilestones}</strong><span>Hackathon milestones</span></div>
                <div><strong>{profile.stats.certifications}</strong><span>Certifications</span></div>
              </div>
              <div className="tech-marks" aria-label="Technologies I work with">
                {techMarks.map(([mark, label]) => (
                  <div key={label} title={label}>
                    <span>{mark}</span>
                    <small>{label}</small>
                  </div>
                ))}
              </div>
            </Reveal>
          </div>
        </div>
      </section>

      <section id="projects" className="section projects-section">
        <div className="container">
          <Reveal>
            <SectionHeading
              eyebrow="Selected work"
              title={<>Projects I’ve <em>built</em></>}
              description="A selection of AI-powered, full-stack, mobile, and data-driven applications."
            />
          </Reveal>
          <div className="project-list">
            {projects.map((project, index) => (
              <ProjectCard key={project.slug} project={project} index={index} />
            ))}
          </div>
        </div>
      </section>

      <section id="skills" className="section skills-section">
        <div className="container">
          <Reveal>
            <SectionHeading
              eyebrow="Skills"
              title={<>What I <em>work with</em></>}
              description="The tools and technologies I use across my project work."
            />
          </Reveal>
          <div className="skills-grid">
            {skillGroups.map((group, index) => (
              <Reveal as="article" className="skill-group" key={group.title} delay={index * 60}>
                <div className="skill-group__heading">
                  <span>{String(index + 1).padStart(2, '0')}</span>
                  <h3>{group.title}</h3>
                </div>
                <div className="tag-row">
                  {group.skills.map((skill) => <span className="tag" key={skill}>{skill}</span>)}
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section id="journey" className="section journey-section">
        <div className="container">
          <Reveal>
            <SectionHeading eyebrow="Journey" title={<>Learning by <em>building</em></>} />
          </Reveal>
          <div className="journey-grid">
            <Reveal className="education-panel">
              <div className="panel-title"><Icon name="book" size={19} /><h3>Education</h3></div>
              <div className="timeline">
                {education.map((item) => (
                  <div className="timeline__item" key={item.institution}>
                    <span className="timeline__dot" aria-hidden="true" />
                    <div>
                      <h4>{item.qualification}</h4>
                      <p>{item.institution}</p>
                      <small>{item.detail}</small>
                    </div>
                  </div>
                ))}
              </div>
            </Reveal>
            <Reveal className="experience-panel" delay={100}>
              <div className="panel-title"><Icon name="briefcase" size={19} /><h3>Project Experience</h3></div>
              <div className="experience-list">
                <div className="experience-item">
                  <span className="experience-item__label">Across six projects</span>
                  <h4>AI, health, safety, finance, and agent systems</h4>
                  <p>Building applications that combine practical interfaces with data, integrations, and real-world workflows.</p>
                  <div className="tag-row">
                    {['AI-powered', 'Full-stack', 'Mobile', 'Data-driven'].map((tag) => <span className="tag" key={tag}>{tag}</span>)}
                  </div>
                </div>
                <div className="experience-item">
                  <span className="experience-item__label">Selected work</span>
                  <h4>LUME · NAYANA · KAWACH · VIORIS · SAATHI · SANCHAY</h4>
                  <p>Each project is linked to its public repository, with live demos where they are currently available.</p>
                </div>
              </div>
            </Reveal>
          </div>
          <Reveal className="certifications" delay={140}>
            <div className="panel-title"><Icon name="award" size={19} /><h3>Courses & Certifications</h3></div>
            <div className="certification-grid">
              {certifications.map((certification) => (
                <div className="certification-card" key={certification.title}>
                  <span className="certification-card__icon"><Icon name="check" size={18} /></span>
                  <div><h4>{certification.title}</h4><p>{certification.issuer}</p></div>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </section>

      <section id="achievements" className="section achievements-section">
        <div className="container">
          <Reveal>
            <SectionHeading eyebrow="Achievements" title={<>Milestones <em>so far</em></>} />
          </Reveal>
          <div className="achievements-grid">
            {achievements.map((achievement, index) => (
              <Reveal as="article" className="achievement-card" key={achievement.title} delay={(index % 3) * 60}>
                <div className="achievement-card__top">
                  <span className="achievement-card__icon"><Icon name="award" size={19} /></span>
                  <span>{String(index + 1).padStart(2, '0')}</span>
                </div>
                <h3>{achievement.title}</h3>
                <strong>{achievement.result}</strong>
                {achievement.description ? <p>{achievement.description}</p> : null}
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section id="repos" className="section repos-section">
        <div className="container">
          <Reveal>
            <SectionHeading
              eyebrow="Learning in public"
              title={<>Code <em>Chronicles</em></>}
              description="A running log of what I’m building and studying. Every repository is a step, not a finished product."
            />
          </Reveal>
          <div className="repo-grid repo-grid--preview">
            {projects.slice(0, 3).map((project, index) => (
              <Reveal as="a" className="repo-card" href={project.repo} target="_blank" rel="noreferrer" key={project.slug} delay={index * 70}>
                <div className="repo-card__top">
                  <Icon name="code" size={19} />
                  <Icon name="arrow-up-right" size={17} />
                </div>
                <h3>{project.name}</h3>
                <p>{project.summary}</p>
                <div className="tag-row">{project.tech.slice(0, 3).map((tech) => <span className="tag" key={tech}>{tech}</span>)}</div>
              </Reveal>
            ))}
          </div>
          <Reveal className="section-link-row">
            <a className="text-link" href="/codechronicles">View More <Icon name="arrow-right" size={17} /></a>
          </Reveal>
        </div>
      </section>

      <section id="contact" className="section contact-section">
        <div className="contact-section__ambient" aria-hidden="true" />
        <div className="container contact-grid">
          <Reveal className="contact-copy">
            <p className="eyebrow"><span aria-hidden="true" /> Contact</p>
            <h2>LET’S<br />GET IN<br /><em>TOUCH</em></h2>
            <p>Have a project in mind or want to discuss an opportunity? Send me a message.</p>
            <div className="contact-details">
              <a href={`mailto:${profile.email}`}><Icon name="mail" size={18} /><span>{profile.email}</span></a>
              <a href={`tel:${profile.phone.replace(/\s/g, '')}`}><Icon name="phone" size={18} /><span>{profile.phone}</span></a>
              <p><Icon name="map-pin" size={18} /><span>{profile.location}</span></p>
            </div>
            <div className="social-row">
              {socialLinks.map((social) => (
                <a key={social.label} href={social.href} target={social.href.startsWith('http') ? '_blank' : undefined} rel={social.href.startsWith('http') ? 'noreferrer' : undefined} aria-label={social.label}>
                  <Icon name={social.icon} size={18} />
                </a>
              ))}
            </div>
          </Reveal>
          <Reveal className="contact-form-wrap" delay={100}>
            <ContactForm />
          </Reveal>
        </div>
      </section>

      <IntroDialog open={introOpen} onClose={() => setIntroOpen(false)} />
    </>
  );
}
