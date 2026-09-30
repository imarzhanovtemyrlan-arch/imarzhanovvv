import "./TabBar.css";

export type Tab = "arbitrage" | "exchanges";

interface Props {
  active: Tab;
  onChange: (tab: Tab) => void;
}

export function TabBar({ active, onChange }: Props) {
  return (
    <nav className="tab-bar">
      <button
        className={`tab-bar__item ${active === "arbitrage" ? "tab-bar__item--active" : ""}`}
        onClick={() => onChange("arbitrage")}
      >
        Arbitrage
      </button>
      <button
        className={`tab-bar__item ${active === "exchanges" ? "tab-bar__item--active" : ""}`}
        onClick={() => onChange("exchanges")}
      >
        Exchanges
      </button>
    </nav>
  );
}
