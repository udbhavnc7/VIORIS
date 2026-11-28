import type { CSSProperties } from 'react';
import type { Project } from '../data/projects';
import { Icon } from './Icon';
import { Reveal } from './Reveal';

type ProjectVisualProps = { project: Project };

export function ProjectVisual({ project }: ProjectVisualProps) {
  return (
    <div className="project-visual" style={{ '--project-accent': project.accent } as CSSProperties}>
      {project.image ? (
        <img src={project.image} alt={`${project.name} application preview`} loading="lazy" />
      ) : (
        <div className="project-visual__generated">
          <div className="project-visual__grid" aria-hidden="true" />
          <div className="project-visual__orb" aria-hidden="true" />
          {project.logo ? (
            <img className="project-visual__logo" src={project.logo} alt="" loading="lazy" />
          ) : (
            <span className="project-visual__monogram">{project.monogram}</span>
          )}
          <div className="project-visual__meta">
            <span>{project.category}</span>
            <strong>{project.name}</strong>
          </div>
        </div>
      )}
      <div className="project-visual__shade" aria-hidden="true" />
      <span className="project-visual__index" aria-hidden="true">
        {project.name.slice(0, 2).toUpperCase()}
      </span>
    </div>
  );
}

type ProjectCardProps = {
  project: Project;
  index: number;
};

export function ProjectCard({ project, index }: ProjectCardProps) {
  return (
    <Reveal as="article" className={`project-card ${index % 2 === 1 ? 'project-card--reverse' : ''}`}>
      <a
        className="project-card__visual-link"
        href={project.demo || project.repo}
        target="_blank"
        rel="noreferrer"
        aria-label={`Open ${project.name} ${project.demo ? 'live demo' : 'repository'}`}
      >
        <ProjectVisual project={project} />
      </a>
      <div className="project-card__content">
        <p className="project-card__category">{project.category}</p>
        <h3>{project.name}</h3>
        <p className="project-card__summary">{project.summary}</p>
        <ul className="project-card__highlights">
          {project.highlights.map((highlight) => (
            <li key={highlight}>
              <Icon name="check" size={15} />
              <span>{highlight}</span>
            </li>
          ))}
        </ul>
        <div className="tag-row">
          {project.tech.map((tech) => (
            <span className="tag" key={tech}>{tech}</span>
          ))}
        </div>
        <div className="project-card__links">
          <a href={project.repo} target="_blank" rel="noreferrer">
            <Icon name="github" size={17} /> Code
          </a>
          {project.demo ? (
            <a href={project.demo} target="_blank" rel="noreferrer">
              Live Demo <Icon name="arrow-up-right" size={16} />
            </a>
          ) : null}
        </div>
      </div>
    </Reveal>
  );
}
