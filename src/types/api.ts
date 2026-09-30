export interface Opportunity {
  id: string;
  symbol: string;
  buy_exchange: string;
  sell_exchange: string;
  buy_price: number;
  sell_price: number;
  gross_spread: number;
  buy_fee: number;
  sell_fee: number;
  estimated_slippage: number;
  net_spread: number;
  available_volume: number;
  recommended_trade_size: number;
  estimated_profit: number;
  timestamp: number;
  data_age: number;
  status: string;
}

export interface ExchangeHealth {
  exchange: string;
  status: "connected" | "connecting" | "disconnected" | "error" | "unsupported";
  markets_count: number;
  last_update: number | null;
  latency_ms: number | null;
  reason: string | null;
}

export interface OrderBookLevel {
  price: number;
  quantity: number;
}

export interface OrderBookSchema {
  exchange: string;
  symbol: string;
  timestamp: number;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
}

export interface OpportunityFilters {
  minNetSpread: number;
  minProfit: number;
  minVolume: number;
}
