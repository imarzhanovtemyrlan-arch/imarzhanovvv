import { useMemo, useState } from "react";
import { FilterBar } from "../components/FilterBar";
import { OpportunityCard } from "../components/OpportunityCard";
import { useOpportunities } from "../hooks/useOpportunities";
import type { Opportunity, OpportunityFilters } from "../types/api";
import "./ArbitragePage.css";

interface Props {
  onOpenDetails: (opportunity: Opportunity) => void;
}

type SortBy = "spread" | "profit" | "volume";

export function ArbitragePage({ onOpenDetails }: Props) {
  const [filters, setFilters] = useState<OpportunityFilters>({
    minNetSpread: 0.03,
    minProfit: 0,
    minVolume: 0,
  });

  const [sortBy, setSortBy] = useState<SortBy>("spread");

  const { opportunities, loading, error } =
    useOpportunities(filters);

  const sortedOpportunities = useMemo(() => {
    return [...opportunities].sort((a, b) => {
      if (sortBy === "profit") {
        return b.estimated_profit - a.estimated_profit;
      }

      if (sortBy === "volume") {
        return b.available_volume - a.available_volume;
      }

      return b.net_spread - a.net_spread;
    });
  }, [opportunities, sortBy]);

  return (
    <div className="arbitrage-page">
      <header className="arbitrage-page__header">
        <h1>Arbitrage</h1>

        <span className="arbitrage-page__count mono">
          {sortedOpportunities.length} live
        </span>
      </header>

      <FilterBar
        filters={filters}
        onChange={setFilters}
      />

      <div className="arbitrage-page__sorting">
        <label className="filter-bar__field">
          <span>Sort by</span>

          <select
            value={sortBy}
            onChange={(e) =>
              setSortBy(e.target.value as SortBy)
            }
          >
            <option value="spread">
              Highest net spread
            </option>

            <option value="profit">
              Highest profit
            </option>

            <option value="volume">
              Highest volume
            </option>
          </select>
        </label>
      </div>

      {loading && (
        <p className="arbitrage-page__status">
          Loading opportunities...
        </p>
      )}

      {error && (
        <p className="arbitrage-page__status arbitrage-page__status--error">
          {error}
        </p>
      )}

      {!loading &&
        !error &&
        sortedOpportunities.length === 0 && (
          <p className="arbitrage-page__status">
            No opportunities clear your filters right now.
          </p>
        )}

      <div className="arbitrage-page__list">
        {sortedOpportunities.map((opp) => (
          <OpportunityCard
            key={opp.id}
            opportunity={opp}
            onOpenDetails={onOpenDetails}
          />
        ))}
      </div>
    </div>
  );
}