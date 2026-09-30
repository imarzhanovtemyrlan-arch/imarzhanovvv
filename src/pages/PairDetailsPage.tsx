import { useEffect, useState } from "react";
import { fetchOrderbook } from "../services/api";
import { formatPercent, formatPrice, formatUsd } from "../services/format";
import type { Opportunity, OrderBookSchema } from "../types/api";
import "./PairDetailsPage.css";

interface Props {
  opportunity: Opportunity;
  onBack: () => void;
}

const SIZE_STEPS = [100, 500, 1000, 5000, 10000];

/** Client-side VWAP re-derivation for the size table, mirroring the backend
 * calculator (app.arbitrage.calculator) so the UI can show all five sizes
 * from a single order book fetch without five round trips. */
function vwapForNotional(levels: { price: number; quantity: number }[], targetNotional: number) {
  let remaining = targetNotional;
  let filledBase = 0;
  let filledQuote = 0;
  for (const level of levels) {
    const levelNotional = level.price * level.quantity;
    if (levelNotional <= remaining) {
      filledBase += level.quantity;
      filledQuote += levelNotional;
      remaining -= levelNotional;
    } else {
      const partialQty = remaining / level.price;
      filledBase += partialQty;
      filledQuote += remaining;
      remaining = 0;
      break;
    }
  }
  const fullyFilled = remaining <= 1e-9;
  const vwapPrice = filledBase > 0 ? filledQuote / filledBase : 0;
  return { vwapPrice, filledQuote, fullyFilled };
}

export function PairDetailsPage({ opportunity, onBack }: Props) {
  const [buyBook, setBuyBook] = useState<OrderBookSchema | null>(null);
  const [sellBook, setSellBook] = useState<OrderBookSchema | null>(null);

  useEffect(() => {
    fetchOrderbook(opportunity.buy_exchange, opportunity.symbol.replace("/", "")).then(setBuyBook);
    fetchOrderbook(opportunity.sell_exchange, opportunity.symbol.replace("/", "")).then(setSellBook);
  }, [opportunity]);

  return (
    <div className="pair-details">
      <header className="pair-details__header">
        <button className="pair-details__back" onClick={onBack}>
          ← Back
        </button>
        <h1>{opportunity.symbol}</h1>
      </header>

      <div className="pair-details__route">
        <span>
          BUY <strong>{opportunity.buy_exchange}</strong> @ {formatPrice(opportunity.buy_price)}
        </span>
        <span>
          SELL <strong>{opportunity.sell_exchange}</strong> @ {formatPrice(opportunity.sell_price)}
        </span>
      </div>

      <table className="pair-details__table">
        <thead>
          <tr>
            <th>Size</th>
            <th>VWAP buy</th>
            <th>VWAP sell</th>
            <th>Net</th>
            <th>Profit</th>
          </tr>
        </thead>
        <tbody>
          {SIZE_STEPS.map((size) => {
            if (!buyBook || !sellBook) {
              return (
                <tr key={size}>
                  <td className="mono">{formatUsd(size)}</td>
                  <td colSpan={4} className="pair-details__loading">
                    loading...
                  </td>
                </tr>
              );
            }
            const buy = vwapForNotional(buyBook.asks, size);
            const sell = vwapForNotional(sellBook.bids, size);
            const grossSpread = buy.vwapPrice > 0 ? (sell.vwapPrice - buy.vwapPrice) / buy.vwapPrice : 0;
            const netSpread = grossSpread - opportunity.buy_fee - opportunity.sell_fee;
            const filled = Math.min(buy.filledQuote, sell.filledQuote);
            const profit = netSpread * filled;
            const capped = !buy.fullyFilled || !sell.fullyFilled;

            return (
              <tr key={size}>
                <td className="mono">
                  {formatUsd(size)}
                  {capped && <span className="pair-details__capped"> *</span>}
                </td>
                <td className="mono">{formatPrice(buy.vwapPrice)}</td>
                <td className="mono">{formatPrice(sell.vwapPrice)}</td>
                <td className="mono">{formatPercent(netSpread)}</td>
                <td className="mono">{formatUsd(profit)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="pair-details__footnote">* limited by available order book depth at this size</p>
    </div>
  );
}
