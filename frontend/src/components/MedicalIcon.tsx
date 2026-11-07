/** Static line icons; no animation or dependencies near visual EEG targets. */
export function MedicalIcon({ name, className = '' }: { name: string; className?: string }) {
  const paths: Record<string, React.ReactNode> = {
    brand: <><rect x="3" y="3" width="18" height="18" rx="5" /><path d="M12 7v10M7 12h10" /></>,
    pain: <><path d="M12 3v4m0 10v4M3 12h4m10 0h4M5.6 5.6l2.8 2.8m7.2 7.2 2.8 2.8M5.6 18.4l2.8-2.8m7.2-7.2 2.8-2.8" /><circle cx="12" cy="12" r="3" /></>,
    breathing: <path d="M12 3v9m0-4c-4-4-8 2-8 8 0 5 8 3 8-1 0 4 8 6 8 1 0-6-4-12-8-8" />,
    'food-water': <path d="M6 8h12l-1 13H7L6 8Zm8 0 2-5h4M5 8h14" />,
    comfort: <path d="M3 18V8m18 10v-6H3m0 4h18M6 12V8h5v4m0-4h7a3 3 0 0 1 3 3v1" />,
    'call-caregiver': <><circle cx="12" cy="7" r="3" /><path d="M5 21v-3a7 7 0 0 1 14 0v3M12 14v6m-3-3h6" /></>,
    back: <path d="m10 5-7 7 7 7M3 12h18" />,
    signal: <path d="M2 12h4l3-8 5 16 3-8h5" />,
    expand: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />,
    contrast: <><circle cx="12" cy="12" r="9" /><path d="M12 3v18" /><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none" /></>,
    settings: <><path d="M4 6h16M4 12h16M4 18h16" /><path d="M8 3v6m8 0v6m-6 0v6" /></>,
    choices: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  }
  return <svg aria-hidden="true" className={`medical-icon ${className}`} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{paths[name] ?? paths.choices}</svg>
}
