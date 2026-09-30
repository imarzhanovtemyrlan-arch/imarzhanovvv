import { useEffect, useState } from "react";
import { ArbitragePage } from "./pages/ArbitragePage";
import { ExchangesPage } from "./pages/ExchangesPage";
import { PairDetailsPage } from "./pages/PairDetailsPage";
import { TabBar, type Tab } from "./components/TabBar";
import { getRawInitData, initTelegramApp } from "./services/telegram";
import { verifyTelegramAuth } from "./services/api";
import type { Opportunity } from "./types/api";
import "./styles/global.css";
import "./App.css";

export default function App() {
  const [tab, setTab] = useState<Tab>("arbitrage");
  const [detailsOpportunity, setDetailsOpportunity] = useState<Opportunity | null>(null);

  useEffect(() => {
    initTelegramApp();
    // Verify identity server-side; never trust getRawInitData() contents
    // directly on the client (see services/telegram.ts).
    const raw = getRawInitData();
    if (raw) {
      verifyTelegramAuth(raw).catch(() => {
        // Non-fatal for monitoring-only mode: the app still works
        // without a verified identity, just without personalization.
      });
    }
  }, []);

  if (detailsOpportunity) {
    return (
      <div className="app-shell">
        <main className="app-shell__content">
          <PairDetailsPage opportunity={detailsOpportunity} onBack={() => setDetailsOpportunity(null)} />
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <main className="app-shell__content">
        {tab === "arbitrage" && <ArbitragePage onOpenDetails={setDetailsOpportunity} />}
        {tab === "exchanges" && <ExchangesPage />}
      </main>
      <TabBar active={tab} onChange={setTab} />
    </div>
  );
}
