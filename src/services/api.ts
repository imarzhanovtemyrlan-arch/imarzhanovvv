import type { ExchangeHealth, Opportunity, OpportunityFilters, OrderBookSchema } from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://43.205.228.189:8000";

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

export function fetchExchanges(): Promise<ExchangeHealth[]> {
  return request("/api/exchanges");
}

export function fetchOpportunities(filters: Partial<OpportunityFilters> = {}): Promise<Opportunity[]> {
  const params = new URLSearchParams();
  if (filters.minNetSpread) params.set("min_net_spread", String(filters.minNetSpread));
  if (filters.minProfit) params.set("min_profit", String(filters.minProfit));
  if (filters.minVolume) params.set("min_volume", String(filters.minVolume));
  const qs = params.toString();
  return request(`/api/opportunities${qs ? `?${qs}` : ""}`);
}

export function fetchOrderbook(exchange: string, symbol: string): Promise<OrderBookSchema> {
  return request(`/api/orderbook/${exchange}/${symbol}`);
}

export interface TelegramAuthResult {
  user: { id: number; first_name?: string; username?: string } | null;
  auth_date: number;
}

/** Sends raw initData to the backend for verification. The frontend never
 * decodes or trusts initData itself - see services/telegram.ts. */
export async function verifyTelegramAuth(initData: string): Promise<TelegramAuthResult | null> {
  if (!initData) return null;
  const res = await fetch(`${API_BASE}/api/auth/telegram`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: initData }),
  });
  if (!res.ok) return null;
  return res.json();
}

export function wsUrl(): string {
  const url = new URL(API_BASE);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws/opportunities";
  return url.toString();
}
