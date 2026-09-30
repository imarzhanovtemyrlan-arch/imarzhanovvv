/**
 * Thin wrapper around the Telegram WebApp SDK (loaded via script tag in
 * index.html). Never trust `initData` on the frontend for anything security
 * sensitive - it must be verified server-side before being used to identify
 * a user (spec section 12).
 */

interface TelegramWebApp {
  ready: () => void;
  expand: () => void;
  colorScheme: "light" | "dark";
  initData: string;
  themeParams: Record<string, string>;
  BackButton: { show: () => void; hide: () => void; onClick: (cb: () => void) => void };
  HapticFeedback?: { impactOccurred: (style: string) => void };
}

declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}

export function getTelegramWebApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null;
}

export function initTelegramApp(): void {
  const webApp = getTelegramWebApp();
  if (!webApp) return; // running outside Telegram (e.g. local dev in a browser)
  webApp.ready();
  webApp.expand();
}

/** Raw initData to send to the backend for server-side verification. Never
 * decode/trust fields from this on the client. */
export function getRawInitData(): string {
  return getTelegramWebApp()?.initData ?? "";
}
