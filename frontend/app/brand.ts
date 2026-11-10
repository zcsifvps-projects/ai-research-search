/**
 * Product name, read from NEXT_PUBLIC_APP_NAME at build time so the brand is never hardcoded.
 * Set it in frontend/.env.production (or .env.local for development) before `npm run build`.
 */
export const APP_NAME = process.env.NEXT_PUBLIC_APP_NAME?.trim() || "Research Engine";

export const APP_TAGLINE = "AI Academic Research & Literature Discovery";
