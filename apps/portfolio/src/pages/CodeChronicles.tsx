import { Link } from 'react-router-dom';
import { Icon } from '../components/Icon';
import { Reveal } from '../components/Reveal';
import { projects } from '../data/projects';

export function CodeChronicles() {
  return (
    <>
      <section className="page-hero chronicles-page-hero">
        <div className="page-hero__ambient" aria-hidden="true" />
        <div className="container">
          <Link className="back-link" to="/"><span>←</span> Back to home</Link>
          <p className="eyebrow"><span aria-hidden="true" /> Learning in public</p>
          <h1>Code <em>Chronicles</em></h1>
          <p>A running log of what I’m building and studying. Every repository here is a step, not a finished product.</p>
        </div>
      </section>
      <section className="section chronicles-index">
        <div className="container">
          <div className="repo-grid">
            {projects.map((project, index) => (
              <Reveal as="a" className="repo-card repo-card--large" href={project.repo} target="_blank" rel="noreferrer" key={project.slug} delay={(index % 3) * 60}>
                <div className="repo-card__top">
                  <Icon name="code" size={20} />
                  <Icon name="arrow-up-right" size={17} />
                </div>
                <h2>{project.name}</h2>
                <p>{project.summary}</p>
                <div className="tag-row">{project.tech.map((tech) => <span className="tag" key={tech}>{tech}</span>)}</div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
