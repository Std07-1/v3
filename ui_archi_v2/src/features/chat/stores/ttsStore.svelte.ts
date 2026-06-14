/**
 * ttsStore — Стан TTS (text-to-speech) сесії.
 *
 * Invariants:
 *   - supported обчислюється один раз при створенні (браузер не підкидає TTS in-flight)
 *   - auto = session-only, НЕ persisted (щоб при reload не злякати тишею/озвучкою)
 *   - lastSpokenId — охороняє від повторного озвучення при маніпуляції messages[]
 *
 * Degraded-but-loud (I7):
 *   - supported=false → UI приховує TTS-контроли, speak() = no-op (без throw)
 *   - toggleAuto() при !supported — ігнорується
 *
 * Consumers: Chat.svelte (автоматичне озвучення archi-повідомлень),
 *            MessageBubble (manual "озвучити" button через onspeak prop).
 */
import type { ChatMessage } from "../../../lib/types";

class TtsStore {
    supported = $state(
        typeof window !== "undefined" && "speechSynthesis" in window,
    );
    auto = $state(false);
    lastSpokenId = $state("");

    /** Озвучити текст (uk-UA, rate 1.05). No-op якщо !supported. */
    speak(text: string): void {
        if (!this.supported) return;
        speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.lang = "uk-UA";
        u.rate = 1.05;
        speechSynthesis.speak(u);
    }

    /** Перемкнути авто-озвучення. Cancel current utterance при вимкненні. */
    toggleAuto(): void {
        if (!this.supported) return;
        this.auto = !this.auto;
        if (!this.auto) speechSynthesis.cancel();
    }

    /**
     * Seed lastSpokenId щоб не читати історію при відкритті вкладки.
     * Викликати ОДИН раз після первинного loadHistory().
     */
    seed(lastArchiId: string): void {
        this.lastSpokenId = lastArchiId;
    }

    /**
     * Якщо auto=true та останнє повідомлення — archi і ще не прозвучало —
     * озвучити його і запам'ятати id. Idempotent при повторних викликах з тим самим
     * messages[].
     */
    maybeAutoSpeak(messages: ChatMessage[]): void {
        if (!this.auto || messages.length === 0) return;
        const last = messages[messages.length - 1];
        if (last.role === "archi" && last.id !== this.lastSpokenId) {
            this.lastSpokenId = last.id;
            this.speak(last.text);
        }
    }
}

/** Singleton — один TTS на вкладку. */
export const ttsStore = new TtsStore();
