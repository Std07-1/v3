// src/ws/connection.ts
// RAIL: teardown без вічного reconnect + parse fail-fast (drop).

import type { RenderFrame, WsAction } from '../types';

export class WSConnection {
  url: string;
  onMessage: (frame: RenderFrame) => void;

  ws: WebSocket | null = null;

  reconnectMs = 200;
  maxReconnectMs = 5000;

  private closedManually = false;

  constructor(url: string, onMessage: (frame: RenderFrame) => void) {
    this.url = url;
    this.onMessage = onMessage;
  }

  connect() {
    this.closedManually = false;
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      console.log('[WS] Connected');
      this.reconnectMs = 200;
    };

    this.ws.onmessage = (event) => {
      try {
        const frame = JSON.parse(event.data) as RenderFrame;
        this.onMessage(frame);
      } catch (err) {
        console.error('[WS] Parse error (dropped):', err);
      }
    };

    this.ws.onclose = () => {
      if (this.closedManually) {
        console.log('[WS] Connection closed manually. No reconnect.');
        return;
      }
      console.warn(`[WS] Disconnected. Reconnecting in ${this.reconnectMs}ms`);
      setTimeout(() => this.connect(), this.reconnectMs);
      this.reconnectMs = Math.min(this.reconnectMs * 1.5, this.maxReconnectMs);
    };

    this.ws.onerror = (err) => console.error('[WS] Error:', err);
  }

  send(payload: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  // ✅ Канон для App/CommandStack
  sendAction(action: WsAction) {
    this.send(action);
  }

  close() {
    this.closedManually = true;
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
  }
}
