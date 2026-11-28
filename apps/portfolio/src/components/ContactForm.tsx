import { useState, type FormEvent } from 'react';
import { profile } from '../data/profile';
import { Icon } from './Icon';

export function ContactForm() {
  const [status, setStatus] = useState<'idle' | 'ready'>('idle');

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get('name') || '').trim();
    const phone = String(form.get('phone') || '').trim();
    const email = String(form.get('email') || '').trim();
    const message = String(form.get('message') || '').trim();
    const subject = encodeURIComponent(`Portfolio enquiry from ${name}`);
    const body = encodeURIComponent(`Hi ${profile.firstName},\n\n${message}\n\n—\n${name}\n${email}${phone ? `\n${phone}` : ''}`);
    window.location.href = `mailto:${profile.email}?subject=${subject}&body=${body}`;
    setStatus('ready');
  };

  return (
    <form className="contact-form" onSubmit={handleSubmit}>
      <div className="contact-form__row">
        <label>
          <span>Full name</span>
          <input name="name" type="text" autoComplete="name" placeholder="Your name" required />
        </label>
        <label>
          <span>Phone <small>optional</small></span>
          <input name="phone" type="tel" autoComplete="tel" placeholder="Your phone number" />
        </label>
      </div>
      <label>
        <span>Email</span>
        <input name="email" type="email" autoComplete="email" placeholder="you@example.com" required />
      </label>
      <label>
        <span>Message</span>
        <textarea name="message" rows={5} placeholder="Tell me what you’d like to build or discuss." required />
      </label>
      <div className="contact-form__footer">
        <button className="button" type="submit">
          Send Message <Icon name="arrow-up-right" size={17} />
        </button>
        <p className="contact-form__note" role="status">
          {status === 'ready'
            ? 'Your email app should open with the message ready to send.'
            : 'This form opens your email app with the message ready to send.'}
        </p>
      </div>
    </form>
  );
}
