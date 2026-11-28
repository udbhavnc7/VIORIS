export const profile = {
  name: 'Udbhav N Chandragiri',
  firstName: 'Udbhav',
  initials: 'UC',
  role: 'Computer Science Engineering (Data Science) student',
  shortRole: 'Developer & CS student',
  location: 'Mangaluru, Karnataka, India',
  email: 'chandragiriudbhav@gmail.com',
  phone: '+91 9916918871',
  github: 'https://github.com/udbhavnc7',
  githubUsername: 'udbhavnc7',
  linkedin: 'https://linkedin.com/in/udbhav-n-c-08d15h4',
  resume: '/resume.pdf',
  intro:
    'I build AI-powered, full-stack, mobile, and data-driven applications — from predictive systems and healthcare technology to safety tools and personal finance.',
  about:
    'I’m a third-year Computer Science Engineering (Data Science) student focused on building AI-powered, full-stack, mobile, and data-driven applications. My project work spans predictive systems, healthcare technology, personal finance, safety applications, and AI agents.',
  aboutSecondary:
    'I’m interested in evidence-backed explanations, permission-aware agents, and practical integrations that make complex systems easier to use.',
  stats: {
    projects: 6,
    hackathonMilestones: 7,
    certifications: 2,
  },
} as const;

export const socialLinks = [
  { label: 'GitHub', href: profile.github, icon: 'github' as const },
  { label: 'LinkedIn', href: profile.linkedin, icon: 'linkedin' as const },
  { label: 'Email', href: `mailto:${profile.email}`, icon: 'mail' as const },
];
