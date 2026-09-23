/**
 * Public thesis snapshot — types + demo data for the trader/investor-facing surface.
 *
 * X28: this is a DELIBERATELY separate shape from Directives/AgentState (lib/types.ts).
 * Those carry private operator state (mood, budget, inner_thought) that must never reach
 * an unauthenticated visitor. The real backend contract is a sanitized public snapshot
 * (`{ns}:public:thesis:{sym}`, composed server-side — not built yet); until it exists,
 * DEMO_THESES below stands in and is rendered as visibly demo data (see Thesis.svelte ribbon).
 */

export type ThesisSymbol = "XAU/USD" | "BTC/USD" | "XAG/USD";
export type ThesisSide = "long" | "short" | null;
export type ThesisState = "active" | "waiting" | "flat";

export interface WatchLevel {
    price: string;
    direction: "вище" | "нижче";
    note: string;
    critical?: boolean;
}

export interface InfluenceFactor {
    label: string;
    detail: string;
    tone: "risk" | "tailwind" | "info";
    tag: string;
}

export interface LadderTarget {
    label: string;
    price: number;
}

export interface PriceLadder {
    min: number;
    max: number;
    invalidation: number;
    entryZone: [number, number];
    now: number;
    targets: LadderTarget[];
}

export interface ThesisSnapshot {
    symbol: ThesisSymbol;
    state: ThesisState;
    side: ThesisSide;
    price: string;
    changePct: string;
    changeUp: boolean;
    /** trusted HTML fragment (EM for emphasis) — render via sanitizeHtml() */
    thesisHtml: string;
    bias: string;
    session: string;
    confidence?: { pct: number; grade: string; why: string };
    ladder?: PriceLadder;
    riskRewardHtml?: string;
    watch: WatchLevel[];
    factors: InfluenceFactor[];
    armedHtml: string;
    updatedAt: string;
}

export const THESIS_SYMBOLS: ThesisSymbol[] = ["XAU/USD", "BTC/USD", "XAG/USD"];

export const DEMO_THESES: Record<ThesisSymbol, ThesisSnapshot> = {
    "XAU/USD": {
        symbol: "XAU/USD",
        state: "active",
        side: "short",
        price: "3 348.5",
        changePct: "−0.42% сьогодні",
        changeUp: false,
        thesisHtml:
            "Золото зняло ліквідність над максимумом четверга і віддало рівень. Чекаю " +
            "<em>відкат у зону 3 352–3 358</em> — звідти шукаю продовження вниз до 3 322.",
        bias: "D1 ведмежий · H4 ведмежий",
        session: "Лондон → Нью-Йорк",
        confidence: { pct: 72, grade: "A", why: "структура + свіп ліквідності; проти — сильний DXY вже в ціні" },
        ladder: {
            min: 3288, max: 3378, invalidation: 3368, entryZone: [3352, 3358], now: 3348.5,
            targets: [{ label: "TP1", price: 3322 }, { label: "TP2", price: 3301 }],
        },
        riskRewardHtml: "Ризик/прибуток від середини зони: <strong>1 : 2.8</strong> до TP1 · <strong>1 : 4.9</strong> до TP2",
        watch: [
            { price: "3 358.0", direction: "вище", note: "верх зони входу — реакція або наскрізь", critical: true },
            { price: "3 368.0", direction: "вище", note: "інвалідація: структура H4 зламана — теза скасована" },
            { price: "3 322.0", direction: "нижче", note: "TP1 · часткова фіксація, стоп у беззбиток" },
            { price: "3 300.0", direction: "нижче", note: "психологічний рівень + тижнева підтримка" },
        ],
        factors: [
            { label: "DXY 104.2 ↑", detail: "сильний долар тисне на золото — за тезу", tone: "tailwind", tag: "за тезу" },
            { label: "US10Y 4.31%", detail: "дохідності ростуть — тримають тиск", tone: "tailwind", tag: "за тезу" },
            { label: "Протокол FOMC", detail: "середа 21:00 Києва — можлива волатильність", tone: "risk", tag: "ризик" },
            { label: "CPI США", detail: "14 липня — головна подія тижня", tone: "info", tag: "календар" },
        ],
        armedHtml:
            "Закріплення H4 над <strong>3 368</strong> скасовує тезу. Вихід протоколу FOMC — Арчі перечитає ринок до входу, не під час.",
        updatedAt: "07:42 Київ",
    },
    "XAG/USD": {
        symbol: "XAG/USD",
        state: "flat",
        side: null,
        price: "39.24",
        changePct: "+0.18% сьогодні",
        changeUp: true,
        thesisHtml:
            "По сріблу тези немає. Ціна в середині діапазону 38.60–39.80 — тут перевага не на боці трейдера. " +
            "<em>Поза ринком.</em>",
        bias: "D1 боковик · H4 боковик",
        session: "—",
        watch: [
            { price: "39.80", direction: "вище", note: "верх діапазону — свіп і реакція дадуть сетап у шорт" },
            { price: "38.60", direction: "нижче", note: "низ діапазону — шукаю реакцію покупця" },
        ],
        factors: [
            { label: "Gold/Silver 85.3", detail: "співвідношення стабільне — срібло без власного драйвера", tone: "info", tag: "нейтрально" },
            { label: "CPI США", detail: "14 липня — може вивести з діапазону", tone: "risk", tag: "ризик" },
        ],
        armedHtml:
            "Дотик <strong>39.80</strong> або <strong>38.60</strong> — Арчі прокинеться, перевірить реакцію і, якщо буде сетап, опублікує тезу.",
        updatedAt: "06:15 Київ",
    },
    "BTC/USD": {
        symbol: "BTC/USD",
        state: "waiting",
        side: "long",
        price: "116 380",
        changePct: "+1.6% сьогодні",
        changeUp: true,
        thesisHtml:
            "Біткоїн утримав злам структури H4 нагору. Чекаю <em>ретест зони попиту 114 200–115 000</em> — " +
            "звідти лонг у бік максимуму 121 000.",
        bias: "D1 бичачий · H4 бичачий",
        session: "будь-яка",
        confidence: { pct: 61, grade: "B", why: "структура за лонг, але зона ще не протестована — вхід не підтверджено" },
        ladder: {
            min: 111500, max: 122500, invalidation: 113400, entryZone: [114200, 115000], now: 116380,
            targets: [{ label: "TP1", price: 119600 }, { label: "TP2", price: 121000 }],
        },
        riskRewardHtml: "Ризик/прибуток від середини зони: <strong>1 : 4.2</strong> до TP1",
        watch: [
            { price: "115 000", direction: "нижче", note: "верх зони попиту — початок сценарію входу", critical: true },
            { price: "113 400", direction: "нижче", note: "інвалідація: зона не втримала — лонг скасовано" },
            { price: "118 200", direction: "вище", note: "пішов без ретесту — тезу переглядаю, не наздоганяю" },
        ],
        factors: [
            { label: "ETF-притоки", detail: "п'ятий тиждень чистих притоків — попит структурний", tone: "tailwind", tag: "за тезу" },
            { label: "Кореляція з SPX", detail: "висока — стежу за апетитом до ризику", tone: "info", tag: "нейтрально" },
            { label: "CPI США", detail: "14 липня — різкий CPI вдарить і по крипті", tone: "risk", tag: "ризик" },
        ],
        armedHtml:
            "Ціна в зоні <strong>114 200–115 000</strong> — Арчі перевірить реакцію на молодших ТФ і підтвердить або скасує вхід. " +
            "Закриття H4 нижче <strong>113 400</strong> — теза скасована.",
        updatedAt: "07:05 Київ",
    },
};

/** win=1 / loss=0, most recent last */
export const DEMO_TRACK_RECORD = {
    cells: [1, 1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1] as const,
    winRate: "67%",
    sampleSize: 42,
    windowLabel: "останні 90 днів",
};
