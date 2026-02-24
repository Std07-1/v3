// src/ws/connection.ts
// Quiet degraded mode: after QUIET_AFTER failed attempts → 60s interval.
// Browser-native WS error logs can't be suppressed, so we minimize attempts.
// DiagState інтеграція: всі on*/close → diagStore.

import type { RenderFrame, WsAction } from '../types';
import { diagStore } from '../app/diagState';
import { startEdgeProbe, stopEdgeProbe } from '../app/edgeProbe';

/** Кілька спроб в швидкому режимі, потім quiet */
const QUIET_AFTER = 3;
const QUIET_INTERVAL_MS = 60_000;

export class WSConnection {
  url: string;
  onMessage: (frame: RenderFrame) => void;
  onOpen: (() => void) | null;

  ws: WebSocket | null = null;

  reconnectMs = 2_000;
  maxReconnectMs = 10_000;

  private closedManually = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private boundOnVisibility: (() => void) | null = null;

  constructor(url: string, onMessage: (frame: RenderFrame) => void, onOpen?: () => void) {
    this.url = url;
    this.onMessage = onMessage;
    this.onOpen = onOpen ?? null;
  }

  connect() {
    this.closedManually = false;
    this.removeVisibilityListener();
    diagStore.setWsState('connecting');
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      console.log('[WS] Connected');
      this.reconnectMs = 2_000;
      diagStore.onWsOpen();
      diagStore.resetReconnectAttempt();
      stopEdgeProbe();
      this.removeVisibilityListener();
      // Reset frame router on every (re)connect so server's new seq=1 isn't stale
      this.onOpen?.();
    };

    this.ws.onmessage = (event) => {
      diagStore.onWsMessage();
      try {
        const frame = JSON.parse(event.data) as RenderFrame;
        this.onMessage(frame);
      } catch (err) {
        console.error('[WS] Parse error (dropped):', err);
      }
    };

    this.ws.onclose = (ev) => {
      diagStore.onWsClose({
        code: ev.code,
        reason: ev.reason,
        wasClean: ev.wasClean,
        ts_ms: Date.now(),
      });

      if (this.closedManually) return;

      const att = diagStore.snapshot().ws_reconnect_attempt;
      if (att <= 1) {
        console.warn(`[WS] Disconnected (code=${ev.code}). Reconnecting...`);
      }
      startEdgeProbe();
      this.scheduleReconnect(att);
    };

    this.ws.onerror = () => {
      const readyState = this.ws?.readyState ?? -1;
      const readableErr = `error (readyState=${readyState}, url=${this.url})`;
      const att = diagStore.snapshot().ws_reconnect_attempt;
      if (att <= 1) {
        console.error(`[WS] ${readableErr}`);
      }
      diagStore.onWsError(readableErr);
    };
  }

  private scheduleReconnect(attempt: number): void {
    this.clearReconnectTimer();
    if (attempt >= QUIET_AFTER) {
      // Quiet mode: рідкі спроби + wake on tab focus
      this.reconnectTimer = setTimeout(() => this.connect(), QUIET_INTERVAL_MS);
      this.addVisibilityListener();
    } else {
      // Fast mode: перші кілька спроб
      this.reconnectTimer = setTimeout(() => this.connect(), this.reconnectMs);
      this.reconnectMs = Math.min(this.reconnectMs * 1.5, this.maxReconnectMs);
    }
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer != null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private addVisibilityListener(): void {
    if (this.boundOnVisibility || typeof document === 'undefined') return;
    this.boundOnVisibility = () => {
      if (!document.hidden && !this.closedManually) {
        this.clearReconnectTimer();
        this.connect();
      }
    };
    document.addEventListener('visibilitychange', this.boundOnVisibility);
  }

  private removeVisibilityListener(): void {
    if (this.boundOnVisibility) {
      document.removeEventListener('visibilitychange', this.boundOnVisibility);
      this.boundOnVisibility = null;
    }
  }

  send(payload: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  sendAction(action: WsAction) {
    this.send(action);
  }

  close() {
    this.closedManually = true;
    this.clearReconnectTimer();
    this.removeVisibilityListener();
    stopEdgeProbe();
    if (this.ws) {
      // Зняти handlers щоб browser не логував помилки
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      // close() тільки якщо ще не закритий
      const rs = this.ws.readyState;
      if (rs === WebSocket.CONNECTING || rs === WebSocket.OPEN) {
        this.ws.close();
      }
      this.ws = null;
      diagStore.setWsState('closed');
    }
  }
}
