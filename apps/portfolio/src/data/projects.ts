export type ProjectCategory = 'AI & Data' | 'HealthTech' | 'Social Impact' | 'AI Agents' | 'Mobile & Finance';

export type Project = {
  slug: string;
  name: string;
  category: ProjectCategory;
  summary: string;
  highlights: string[];
  tech: string[];
  repo: string;
  demo?: string;
  image?: string;
  logo?: string;
  monogram: string;
  accent: string;
};

export const projects: Project[] = [
  {
    slug: 'lume',
    name: 'LUME',
    category: 'AI & Data',
    summary:
      'Land-acquisition milestone delay prediction using project signals and evidence-backed explanations.',
    highlights: [
      'Predicts whether the next land-acquisition milestone is likely to be delayed.',
      'Explains the evidence behind each prediction and is deployed on Render.',
    ],
    tech: ['TypeScript', 'Render'],
    repo: 'https://github.com/udbhavnc7/LUME',
    demo: 'https://lume-0n9h.onrender.com/',
    image: '/images/lume.png',
    monogram: 'LU',
    accent: '#6ee7d8',
  },
  {
    slug: 'nayana',
    name: 'NAYANA',
    category: 'HealthTech',
    summary:
      'Gaze-tracking clinical platform for eye-driven communication and real-time clinician telemetry.',
    highlights: [
      'Turns eye movements into communication for patients with limited mobility or speech.',
      'Provides clinicians with a real-time telemetry dashboard for remote monitoring.',
    ],
    tech: ['JavaScript', 'Vercel'],
    repo: 'https://github.com/udbhavnc7/Nayana',
    monogram: 'NA',
    accent: '#8fb7ff',
  },
  {
    slug: 'kawach',
    name: 'KAWACH',
    category: 'Social Impact',
    summary: 'Smart women safety system with silent SOS, guardian alerts, and live location.',
    highlights: [
      'Combines silent SOS, guardian alerts, live location, fake calls, and emergency communication.',
      'Integrates Google Maps, Geolocator, FCM, and speech recognition.',
    ],
    tech: ['React', 'Flutter', 'Firebase', 'Supabase'],
    repo: 'https://github.com/udbhavnc7/KAWACH',
    demo: 'https://kawach-safety.netlify.app',
    logo: '/images/kawach.png',
    monogram: 'KA',
    accent: '#f0a56b',
  },
  {
    slug: 'vioris',
    name: 'VIORIS',
    category: 'AI Agents',
    summary:
      'Private, auditable voice-first agent with planning, memory, specialist agents, connectors, device control, and permission management.',
    highlights: [
      'Coordinates voice, planning, memory, specialist agents, connectors, and device control.',
      'Uses a strict permission engine and audit trail for externally visible actions.',
    ],
    tech: ['Python'],
    repo: 'https://github.com/udbhavnc7/VIORIS',
    monogram: 'VI',
    accent: '#d4af7a',
  },
  {
    slug: 'saathi',
    name: 'SAATHI',
    category: 'HealthTech',
    summary:
      'Voice-first recovery companion with prescription OCR, medication inventory, deterministic symptom triage, and caretaker synchronization.',
    highlights: [
      'Supports multilingual post-discharge care with prescription OCR and medication inventory.',
      'Uses deterministic symptom triage and caretaker synchronization.',
    ],
    tech: ['React', 'TypeScript', 'Node.js', 'Gemini AI'],
    repo: 'https://github.com/udbhavnc7/SAATHI',
    logo: '/images/saathi-logo.png',
    monogram: 'SA',
    accent: '#9ed6b4',
  },
  {
    slug: 'sanchay',
    name: 'SANCHAY',
    category: 'Mobile & Finance',
    summary:
      'Customized Ivy Wallet-based Android finance app for income, expenses, budgets, and financial goals.',
    highlights: [
      'Tracks income, expenses, budgets, and financial goals.',
      'Built with Kotlin, Jetpack Compose, Room, and Hilt.',
    ],
    tech: ['Kotlin', 'Jetpack Compose', 'Room', 'Hilt'],
    repo: 'https://github.com/udbhavnc7/Sanchay',
    demo: 'https://sanchay-raib.onrender.com',
    image: '/images/sanchay.png',
    monogram: 'SA',
    accent: '#f2b36d',
  },
];

export const projectFilters: Array<'All' | ProjectCategory> = [
  'All',
  'AI & Data',
  'HealthTech',
  'Social Impact',
  'AI Agents',
  'Mobile & Finance',
];
