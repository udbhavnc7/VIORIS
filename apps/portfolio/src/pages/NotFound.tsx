import { Link } from 'react-router-dom';
import { Icon } from '../components/Icon';

export function NotFound() {
  return (
    <section className="not-found">
      <div className="container">
        <p className="eyebrow"><span aria-hidden="true" /> 404</p>
        <h1>This page wandered <em>off</em>.</h1>
        <p>The page you’re looking for doesn’t exist or may have moved.</p>
        <Link className="button" to="/">Back to home <Icon name="arrow-right" size={17} /></Link>
      </div>
    </section>
  );
}
