import type { ReactNode, SVGProps } from 'react';

export type IconName =
  | 'github'
  | 'linkedin'
  | 'mail'
  | 'arrow-up-right'
  | 'arrow-right'
  | 'download'
  | 'menu'
  | 'close'
  | 'chevron-down'
  | 'code'
  | 'play'
  | 'map-pin'
  | 'phone'
  | 'award'
  | 'book'
  | 'briefcase'
  | 'sparkles'
  | 'check'
  | 'copy'
  | 'external';

type IconProps = SVGProps<SVGSVGElement> & { name: IconName; size?: number };

export function Icon({ name, size = 20, ...props }: IconProps) {
  const paths: Record<IconName, ReactNode> = {
    github: (
      <path d="M12 .7a11.5 11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2.17c-3.22.7-3.9-1.37-3.9-1.37-.52-1.33-1.28-1.68-1.28-1.68-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.57-.29-5.27-1.29-5.27-5.73 0-1.27.45-2.3 1.19-3.11-.12-.29-.52-1.47.11-3.05 0 0 .97-.31 3.16 1.19a10.9 10.9 0 0 1 5.76 0c2.19-1.5 3.16-1.19 3.16-1.19.63 1.58.23 2.76.11 3.05.74.81 1.19 1.84 1.19 3.11 0 4.45-2.71 5.43-5.29 5.72.42.36.79 1.07.79 2.16v3.2c0 .31.21.67.8.56A11.5 11.5 0 0 0 12 .7Z" />
    ),
    linkedin: (
      <path d="M20.45 20.45h-3.56v-5.57c0-1.33-.03-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 2.94v5.67H9.34V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28ZM5.34 7.43a2.06 2.06 0 1 1 0-4.12 2.06 2.06 0 0 1 0 4.12Zm1.78 13.02H3.56V9h3.56v11.45ZM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.22 0Z" />
    ),
    mail: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="m3 7 9 6 9-6" />
      </>
    ),
    'arrow-up-right': <path d="M7 17 17 7M7 7h10v10" />,
    'arrow-right': <path d="M4 12h16m-6-6 6 6-6 6" />,
    download: <path d="M12 3v12m-5-5 5 5 5-5M5 21h14" />,
    menu: <path d="M4 7h16M4 12h16M4 17h16" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    'chevron-down': <path d="m6 9 6 6 6-6" />,
    code: <path d="m8 9-3 3 3 3m8-6 3 3-3 3m-3-9-2 12" />,
    play: <path d="m9 7 8 5-8 5V7Z" />,
    'map-pin': (
      <>
        <path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z" />
        <circle cx="12" cy="10" r="2.5" />
      </>
    ),
    phone: <path d="M6.6 3h2.2l1.2 4-1.7 1.2a15 15 0 0 0 6.7 6.7l1.2-1.7 4 1.2v2.2a2.6 2.6 0 0 1-2.9 2.6A17.6 17.6 0 0 1 4 6.9 2.6 2.6 0 0 1 6.6 3Z" />,
    award: (
      <>
        <circle cx="12" cy="8" r="5" />
        <path d="m8.5 12-1 9 4.5-2.5 4.5 2.5-1-9" />
      </>
    ),
    book: (
      <>
        <path d="M4 4h6a3 3 0 0 1 3 3v14a3 3 0 0 0-3-3H4V4Z" />
        <path d="M20 4h-4a3 3 0 0 0-3 3v14a3 3 0 0 1 3-3h4V4Z" />
      </>
    ),
    briefcase: (
      <>
        <rect x="3" y="7" width="18" height="13" rx="2" />
        <path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18" />
      </>
    ),
    sparkles: <path d="m12 3 1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3Zm7 11 .8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8L19 14ZM5 14l.8 2.2L8 17l-2.2.8L5 20l-.8-2.2L2 17l2.2-.8L5 14Z" />,
    check: <path d="m5 12 4 4L19 6" />,
    copy: (
      <>
        <rect x="9" y="9" width="11" height="11" rx="2" />
        <path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3" />
      </>
    ),
    external: <path d="M14 4h6v6M20 4 10 14M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" />,
  };

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}
