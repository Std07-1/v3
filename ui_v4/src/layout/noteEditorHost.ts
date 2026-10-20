// src/layout/noteEditorHost.ts
// ADR-0111 §3.7: поле вводу нотатки (NoteEditor.svelte) — одне на сторінку, монтується в body на запит renderer-а
// (DrawingsRenderer.onNoteText). Поза ChartPane/App: обидва файли біля межі X33. Фокус ставиться одразу, у тому самому
// жесті (клік, тап, ✓) — інакше телефон не відкриє клавіатуру.

import { flushSync, mount, unmount } from 'svelte';
import type { NoteTextRequest } from '../chart/drawings/noteText';
import NoteEditor from './NoteEditor.svelte';

let current: { instance: Record<string, unknown>; host: HTMLElement } | null = null;

/** Відкрити поле для запиту; попереднє (якщо ще відкрите) закривається без збереження. */
export function openNoteEditor(request: NoteTextRequest): void {
  closeNoteEditor();
  const host = document.createElement('div');
  document.body.appendChild(host);
  const instance = mount(NoteEditor, { target: host, props: { request, onClose: closeNoteEditor } });
  current = { instance, host };
  flushSync();
  host.querySelector('input')?.focus();
}

/** Закрити поле (після збереження чи скасування). */
export function closeNoteEditor(): void {
  if (!current) return;
  const { instance, host } = current;
  current = null;
  void unmount(instance);
  host.remove();
}
