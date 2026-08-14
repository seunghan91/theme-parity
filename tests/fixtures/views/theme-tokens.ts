// Per-user themes live in TypeScript and are applied as inline style at
// runtime. The names below are real definitions even though no stylesheet
// mentions them. Scanning refs in .ts while ignoring declarations in .ts
// reports every one of them as undefined — a whole component family at once.
export interface ProfileTokens {
  '--ts-card-bg': string
  '--ts-card-text': string
}

export const THEMES: Record<string, ProfileTokens> = {
  paper: { '--ts-card-bg': '#F8FAFC', '--ts-card-text': '#0F172A' },
  ink: { '--ts-card-bg': '#1A1A1A', '--ts-card-text': '#F5F5F5' },
}
