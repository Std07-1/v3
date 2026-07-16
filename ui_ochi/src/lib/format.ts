/**
 * Формат-хелпери «Очі Арчі». ТІЛЬКИ presentation: час у «N хв тому», абсолютний
 * рядок для title, компактні числа (ціна/Δ/токени/$). Домен не рахуємо (X28) —
 * знаки/значення приходять з бекенда, тут лише читабельне форматування.
 */

/** «щойно» / «N хв тому» / «N год тому» / «N дн тому» від epoch-ms відносно now. */
export function timeAgo(tsMs: number, nowMs: number = Date.now()): string {
    if (!Number.isFinite(tsMs) || tsMs <= 0) return '';
    const deltaSec = Math.round((nowMs - tsMs) / 1000);
    if (deltaSec < 0) return 'щойно';
    if (deltaSec < 45) return 'щойно';
    const min = Math.round(deltaSec / 60);
    if (min < 60) return `${min} хв тому`;
    const hours = Math.floor(min / 60);
    if (hours < 24) return `${hours} год тому`;
    const days = Math.floor(hours / 24);
    return `${days} дн тому`;
}

/** Абсолютний локальний штамп для title-атрибута (повна дата+час). */
export function absTime(tsMs: number): string {
    if (!Number.isFinite(tsMs) || tsMs <= 0) return '';
    return new Date(tsMs).toLocaleString('uk-UA', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
}

/** «за ~N хв» / «за ~N год M хв» / «ось-ось» від майбутнього epoch-ms. */
export function timeUntil(tsMs: number, nowMs: number = Date.now()): string {
    if (!Number.isFinite(tsMs) || tsMs <= 0) return '';
    const min = Math.round((tsMs - nowMs) / 60_000);
    if (min < -3) return '';
    if (min <= 0) return 'ось-ось';
    if (min < 60) return `за ~${min} хв`;
    const hours = Math.floor(min / 60);
    const rest = min % 60;
    return rest ? `за ~${hours} год ${rest} хв` : `за ~${hours} год`;
}

/** Ціна з розумною к-стю знаків (золото ~4700.5, крипта може мати більше). */
export function fmtPrice(value: number): string {
    if (!Number.isFinite(value)) return '—';
    const decimals = Math.abs(value) >= 100 ? 2 : 4;
    return value.toLocaleString('uk-UA', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

/** Знакова Δ до ціни: «+19.5» / «−0.5» (сервер уже порахував число, X28). */
export function fmtDelta(value: number): string {
    if (!Number.isFinite(value)) return '—';
    const sign = value > 0 ? '+' : value < 0 ? '−' : '';
    return `${sign}${fmtPrice(Math.abs(value))}`;
}

/** Δ у відсотках: сервер дає частку (0.0106 → «1.06%»). */
export function fmtPct(fraction: number): string {
    if (!Number.isFinite(fraction)) return '';
    return `${(fraction * 100).toFixed(2)}%`;
}

/** Компактні токени: 12000 → «12.0k», 800 → «800». */
export function fmtTokens(value: number | undefined): string {
    if (value == null || !Number.isFinite(value)) return '0';
    if (value < 1000) return String(value);
    return `${(value / 1000).toFixed(1)}k`;
}

/** Вартість виклику: $0.021 → «$0.021», $0 → «$0». Дрібні значення без «0.00». */
export function fmtCost(value: number | undefined): string {
    if (value == null || !Number.isFinite(value)) return '$0';
    if (value === 0) return '$0';
    if (value < 0.001) return '<$0.001';
    return `$${value.toFixed(3)}`;
}
