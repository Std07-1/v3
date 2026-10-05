// src/lib/priceDigits.ts
// Точність ціни символу (ADR-0054 rev 7 §3.4.1 S3): сервер кладе `price_digits` з config.json:price_display — digits
// таблиці OFFERS FXCM (XAG/USD і USD/JPY — 3 знаки, решта — 2). UI не вгадує точність за величиною ціни (X28).
// Символ без значення в конфігу — стара евристика за величиною; frameRouter про це вже попередив.

/** Межа розумного, як `PRICE_DIGITS_MAX` у core/config_loader.py: ловить кривий кадр, а не обмежує брокера. */
export const PRICE_DIGITS_MAX = 8;

export type PriceDigits = Record<string, number>;

/** Поле `price_digits` кадру конфігу → мапа символ → знаки; не той вигляд — null (frameRouter попереджає). */
export function parsePriceDigits(raw: unknown): PriceDigits | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const digitsBySymbol: PriceDigits = {};
  for (const [symbol, digits] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof digits !== 'number' || !Number.isInteger(digits) || digits < 0 || digits > PRICE_DIGITS_MAX) return null;
    digitsBySymbol[symbol] = digits;
  }
  return digitsBySymbol;
}

/** Знаки символу з конфігу сервера; null — конфіг ще не прийшов або символу в ньому нема. */
export function configuredPriceDigits(digitsBySymbol: PriceDigits | undefined, symbol: string | null | undefined): number | null {
  if (!digitsBySymbol || !symbol) return null;
  return digitsBySymbol[symbol] ?? null;
}

/** Знаки для показу `value`: з конфігу, а без нього — стара евристика за величиною (≥100 → 2, ≥10 → 3, інакше 5). */
export function displayPriceDigits(configured: number | null, value: number): number {
  if (configured !== null) return configured;
  const magnitude = Math.abs(value);
  if (magnitude >= 100) return 2;
  if (magnitude >= 10) return 3;
  return 5;
}
