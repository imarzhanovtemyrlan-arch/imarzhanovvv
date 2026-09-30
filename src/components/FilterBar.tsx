import { useState } from "react";
import type { OpportunityFilters } from "../types/api";
import "./FilterBar.css";

interface Props {
  filters: OpportunityFilters;
  onChange: (filters: OpportunityFilters) => void;
}

const VOLUME_STEPS = [0, 100, 500, 1000, 5000];

export function FilterBar({ filters, onChange }: Props) {
  const [spreadInput, setSpreadInput] = useState(
    String(filters.minNetSpread * 100)
  );

  const [volumeInput, setVolumeInput] = useState(
    String(filters.minVolume)
  );

  const handleApply = () => {
    let spread = Number(spreadInput);

    if (!Number.isFinite(spread)) {
      spread = 0.01;
    }

    spread = Math.min(99999, Math.max(0.01, spread));

    let volume = Number(volumeInput);

    if (!Number.isFinite(volume)) {
      volume = 0;
    }

    onChange({
      ...filters,
      minNetSpread: spread / 100,
      minVolume: volume,
    });

    setSpreadInput(String(spread));
  };

  return (
    <div className="filter-bar">
      <label className="filter-bar__field">
        <span>Min net spread (%)</span>

        <input
          type="text"
          inputMode="decimal"
          value={spreadInput}
          placeholder="0.01"
          onChange={(e) => {
            const value = e.target.value;

            if (/^\d*\.?\d*$/.test(value)) {
              setSpreadInput(value);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              handleApply();
            }
          }}
        />
      </label>

      <label className="filter-bar__field">
        <span>Min volume</span>

        <select
          value={volumeInput}
          onChange={(e) => setVolumeInput(e.target.value)}
        >
          {VOLUME_STEPS.map((value) => (
            <option key={value} value={value}>
              ${value}
            </option>
          ))}
        </select>
      </label>

      <button
        type="button"
        className="filter-bar__apply"
        onClick={handleApply}
      >
        Apply
      </button>
    </div>
  );
}