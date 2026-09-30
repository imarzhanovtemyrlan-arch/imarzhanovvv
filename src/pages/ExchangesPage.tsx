import { useEffect, useState } from "react";
import { fetchExchanges } from "../services/api";
import { StatusBadge } from "../components/StatusBadge";
import type { ExchangeHealth } from "../types/api";
import "./ExchangesPage.css";

export function ExchangesPage() {
  const [exchanges, setExchanges] = useState<ExchangeHealth[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    function load() {
      fetchExchanges()
        .then((data) => !cancelled && setExchanges(data))
        .finally(() => !cancelled && setLoading(false));
    }
    load();
    const interval = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="exchanges-page">
      <header className="exchanges-page__header">
        <h1>Exchanges</h1>
      </header>

      {loading && <p className="exchanges-page__status">Loading...</p>}

      <ul className="exchanges-page__list">
        {exchanges.map((ex) => (
          <li key={ex.exchange} className="exchange-row">
            <div className="exchange-row__main">
              <span className="exchange-row__name">{ex.exchange}</span>
              <StatusBadge status={ex.status} />
            </div>
            <div className="exchange-row__meta mono">
              <span>{ex.markets_count.toLocaleString()} markets</span>
              {ex.latency_ms != null && <span>{ex.latency_ms.toFixed(0)} ms</span>}
            </div>
            {ex.reason && <p className="exchange-row__reason">{ex.reason}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}
