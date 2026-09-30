import type { Opportunity } from "../types/api";
import { formatAge, formatPercent, formatPrice, formatUsd } from "../services/format";
import "./OpportunityCard.css";

interface Props {
  opportunity: Opportunity;
  onOpenDetails: (opportunity: Opportunity) => void;
}

export function OpportunityCard({ opportunity, onOpenDetails }: Props) {
  const isFresh = opportunity.data_age < 2;

  return (
    <article className="opp-card">
      <header className="opp-card__head">
        <span className="opp-card__symbol">{opportunity.symbol}</span>
        <span className={`opp-card__age ${isFresh ? "opp-card__age--fresh" : ""}`}>
          {formatAge(opportunity.data_age)}
        </span>
      </header>

      <div className="opp-card__route">
        <div className="opp-card__leg">
          <span className="opp-card__leg-label">BUY</span>
          <span className="opp-card__leg-exchange">{opportunity.buy_exchange}</span>
          <span className="opp-card__leg-price mono">{formatPrice(opportunity.buy_price)}</span>
        </div>
        <div className="opp-card__arrow" aria-hidden>
          ↝
        </div>
        <div className="opp-card__leg">
          <span className="opp-card__leg-label">SELL</span>
          <span className="opp-card__leg-exchange">{opportunity.sell_exchange}</span>
          <span className="opp-card__leg-price mono">{formatPrice(opportunity.sell_price)}</span>
        </div>
      </div>

      <dl className="opp-card__metrics">
        <div>
          <dt>Gross</dt>
          <dd className="mono">{formatPercent(opportunity.gross_spread)}</dd>
        </div>
        <div>
          <dt>Fees</dt>
          <dd className="mono">{formatPercent(opportunity.buy_fee + opportunity.sell_fee)}</dd>
        </div>
        <div>
          <dt>Slippage</dt>
          <dd className="mono">{formatPercent(opportunity.estimated_slippage)}</dd>
        </div>
        <div className="opp-card__metric--net">
          <dt>Net</dt>
          <dd className="mono">{formatPercent(opportunity.net_spread)}</dd>
        </div>
      </dl>

      <footer className="opp-card__foot">
        <div className="opp-card__volume">
          <span className="opp-card__volume-label">Volume</span>
          <span className="mono">{formatUsd(opportunity.available_volume)}</span>
        </div>
        <div className="opp-card__profit">
          <span className="opp-card__profit-label">Est. profit</span>
          <span className="mono opp-card__profit-value">{formatUsd(opportunity.estimated_profit)}</span>
        </div>
        <button className="opp-card__details" onClick={() => onOpenDetails(opportunity)}>
          Details
        </button>
      </footer>
    </article>
  );
}
