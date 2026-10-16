// src/lib/alerts/alertSound.ts
// ADR-0114 §4.4: короткий тихий сигнал спрацювання на відкритій сторінці — два тони WebAudio, без звукового файлу.
// Браузер дозволяє звук лише після жесту людини: контекст створюється на першому pointerdown. До того — без звуку,
// і про це одне повідомлення в консоль (не тиша без пояснення).

const TONES_HZ = [880, 660] as const;
const TONE_S = 0.15; // тривалість одного тону
const GAP_S = 0.16; // крок між тонами
const PEAK_GAIN = 0.06; // тихо: сигнал, а не тривога

let context: AudioContext | null = null;
let lockedReported = false;

/** Готує звук до першого жесту на сторінці; повертає відписку. */
export function armAlertSound(target: EventTarget = window): () => void {
  const unlock = () => {
    try {
      context ??= new AudioContext();
      void context.resume();
    } catch (err) {
      console.warn('[alerts] ALERTS_SOUND_UNAVAILABLE %s', String(err));
    }
  };
  target.addEventListener('pointerdown', unlock, { once: true, passive: true });
  return () => target.removeEventListener('pointerdown', unlock);
}

export function playAlertChime(): void {
  if (context === null || context.state !== 'running') {
    if (!lockedReported) {
      lockedReported = true;
      console.info('[alerts] ALERTS_SOUND_LOCKED — браузер не дає звук до першого дотику до сторінки');
    }
    return;
  }
  const start = context.currentTime;
  TONES_HZ.forEach((hz, i) => {
    const at = start + i * GAP_S;
    const tone = context!.createOscillator();
    const gain = context!.createGain();
    tone.type = 'sine';
    tone.frequency.value = hz;
    gain.gain.setValueAtTime(0, at);
    gain.gain.linearRampToValueAtTime(PEAK_GAIN, at + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + TONE_S);
    tone.connect(gain).connect(context!.destination);
    tone.start(at);
    tone.stop(at + TONE_S + 0.01);
  });
}
