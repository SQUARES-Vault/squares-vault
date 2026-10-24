/**
 * Central configuration for SQUARES Vault Marketing Site.
 * Single source of truth for URLs, contact information, and feature flags.
 */

export const siteConfig = {
  // Deploy target config: change base to '' when moving to a custom domain.
  siteUrl: 'https://squares-vault.github.io',
  basePath: '/squares-vault',

  // Brand and Metadata
  siteName: 'SQUARES Vault',
  tagline: 'Private Life-Admin & Intelligent Document Assistant',
  description:
    'A Telegram assistant that reads household documents with Gemini AI, stores the original files in your personal Google Drive, and sends automated expiry reminders.',
  googleSiteVerification: 'nhyGz_6hp1YBNQgTZzzW-jW0_WXk9VBi-8D8xBNEja4',

  // Contact & Attribution
  contactEmail: 'shikharsinghpatel07@gmail.com',
  builderName: 'SHIKHAR SINGH PATEL',
  botUsername: '@squaresvaultbot',
  botUrl: 'https://t.me/squaresvaultbot',

  // Pricing Fact
  pricingNotice:
    'Free during early access. Core features (Limited AI document reading, vault, expiry alerts) will stay free. Optional paid plans may come later.',

  // External Form & Analytics (Disabled by default until owner provides endpoint / ID)
  formspreeEndpoint: '', // E.g. "https://formspree.io/f/xyz..."
  analytics: {
    enabled: false,
    plausibleDomain: '', // e.g. "squaresvault.com"
    gaMeasurementId: '', // e.g. "G-XXXXXXXXXX"
  },
} as const;

/**
 * Helper to construct internal URLs respecting basePath.
 * When switching to a custom domain, basePath can be set to '' in one place.
 */
export function getAssetUrl(path: string): string {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${siteConfig.basePath}${cleanPath}`;
}

export function getCanonicalUrl(path: string): string {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${siteConfig.siteUrl}${siteConfig.basePath}${cleanPath}`;
}
