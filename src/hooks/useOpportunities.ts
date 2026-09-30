import { useEffect, useRef, useState } from "react";
import { fetchOpportunities, wsUrl } from "../services/api";
import type { Opportunity, OpportunityFilters } from "../types/api";

interface WsPayload {
  type: "opportunities_update";
  pair: string;
  opportunities: Opportunity[];
}

export function useOpportunities(filters: Partial<OpportunityFilters>) {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const byId = useRef<Map<string, Opportunity>>(new Map());

  // Всегда храним актуальный фильтр для WebSocket.
  const filtersRef = useRef(filters);

  useEffect(() => {
    filtersRef.current = filters;
  }, [filters]);

  // REST-запрос при изменении фильтра.
  useEffect(() => {
    let cancelled = false;

    setLoading(true);

    fetchOpportunities(filters)
      .then((data) => {
        if (cancelled) return;

        const minSpread = filters.minNetSpread ?? 0;
        const minProfit = filters.minProfit ?? 0;
        const minVolume = filters.minVolume ?? 0;

        const filtered = data.filter(
          (opp) =>
            opp.net_spread >= minSpread &&
            opp.estimated_profit >= minProfit &&
            opp.available_volume >= minVolume
        );

        byId.current = new Map(
          filtered.map((opp) => [opp.id, opp])
        );

        setOpportunities(filtered);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(String(err));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [filters.minNetSpread, filters.minProfit, filters.minVolume]);

  // WebSocket.
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closedByUs = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (closedByUs) return;

      ws = new WebSocket(wsUrl());

      ws.onmessage = (event) => {
        try {
          const payload: WsPayload = JSON.parse(event.data);

          if (payload.type !== "opportunities_update") {
            return;
          }

          const currentFilters = filtersRef.current;

          const minSpread = currentFilters.minNetSpread ?? 0;
          const minProfit = currentFilters.minProfit ?? 0;
          const minVolume = currentFilters.minVolume ?? 0;

          // Только opportunities, которые проходят
          // ТЕКУЩИЙ фильтр.
          const filteredIncoming = payload.opportunities.filter(
            (opp) =>
              opp.net_spread >= minSpread &&
              opp.estimated_profit >= minProfit &&
              opp.available_volume >= minVolume
          );

          const incomingIds = new Set(
            filteredIncoming.map((opp) => opp.id)
          );

          // Удаляем старые opportunities этого pair,
          // которые больше не проходят фильтр
          // или исчезли из текущего WS-пакета.
          for (const [id, opp] of byId.current) {
            if (
              opp.symbol === payload.pair &&
              !incomingIds.has(id)
            ) {
              byId.current.delete(id);
            }
          }

          // Добавляем только opportunities,
          // которые прошли фильтр.
          for (const opp of filteredIncoming) {
            byId.current.set(opp.id, opp);
          }

          setOpportunities(
            Array.from(byId.current.values())
          );
        } catch {
          // Игнорируем некорректные WebSocket frames.
        }
      };

      ws.onerror = () => {
        ws?.close();
      };

      ws.onclose = () => {
        if (!closedByUs) {
          reconnectTimer = setTimeout(connect, 2000);
        }
      };
    }

    connect();

    return () => {
      closedByUs = true;

      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }

      ws?.close();
    };
  }, []);

  return {
    opportunities,
    loading,
    error,
  };
}