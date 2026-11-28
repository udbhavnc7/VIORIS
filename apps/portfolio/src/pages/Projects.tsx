import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from '../components/Icon';
import { ProjectVisual } from '../components/ProjectCard';
import { Reveal } from '../components/Reveal';
import { profile } from '../data/profile';
import { projectFilters, projects, type ProjectCategory } from '../data/projects';

export function Projects() {
  const [filter, setFilter] = useState<'All' | ProjectCategory>('All');
  const visibleProjects = filter === 'All' ? projects : projects.filter((project) => project.category === filter);

  return (
    <>
      <section className="page-hero projects-page-hero">
        <div className="page-hero__ambient" aria-hidden="true" />
        <div className="container">
          <Link className="back-link" to="/"><span>←</span> Back to home</Link>
          <p className="eyebrow"><span aria-hidden="true" /> Selected work</p>
          <h1>Featured <em>Projects</em></h1>
          <p>Explore a portfolio of AI-powered applications, automation tools, full-stack systems, and mobile experiences.</p>
          <div className="filter-row" role="group" aria-label="Filter projects">
            {projectFilters.map((item) => (
              <button
                type="button"
                key={item}
                className={filter === item ? 'is-active' : ''}
                aria-pressed={filter === item}
                onClick={() => setFilter(item)}
              >
                {item}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="section projects-index">
        <div className="container">
          <div className="projects-index__grid">
            {visibleProjects.map((project, index) => (
              <Reveal as="article" className="project-grid-card" key={project.slug} delay={(index % 3) * 60}>
                <a href={project.demo || project.repo} target="_blank" rel="noreferrer" aria-label={`Open ${project.name}`}>
                  <ProjectVisual project={project} />
                </a>
                <div className="project-grid-card__body">
                  <p className="project-card__category">{project.category}</p>
                  <h2>{project.name}</h2>
                  <p>{project.summary}</p>
                  <div className="tag-row">{project.tech.map((tech) => <span className="tag" key={tech}>{tech}</span>)}</div>
                  <div className="project-card__links">
                    <a href={project.repo} target="_blank" rel="noreferrer"><Icon name="github" size={16} /> Code</a>
                    {project.demo ? <a href={project.demo} target="_blank" rel="noreferrer">Live Demo <Icon name="arrow-up-right" size={15} /></a> : null}
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
          <p className="projects-index__note">Looking for the full background? <a href={profile.resume} download>Download my resume</a>.</p>
        </div>
      </section>
    </>
  );
}
