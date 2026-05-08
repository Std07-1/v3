// src/data/oss-notices.ts
// ADR-0066 PATCH 04: OSS dependency NOTICE list rendered in AboutModal Credits tab.
// Closes TradingView lightweight-charts Apache 2.0 attribution requirement
// per Tier 7 (attributionLogo:false in PATCH 05 + Credits surface here).
//
// SSOT for license/attribution disclosures. Update when new runtime deps land.

export interface OssNotice {
    /** Package name (npm) */
    name: string;
    /** SPDX license identifier */
    license: string;
    /** Copyright holder + year (verbatim from package LICENSE) */
    copyright: string;
    /** Source repository URL */
    homepage: string;
    /** Optional verbatim NOTICE text (Apache 2.0 §4(d) requirement). null if no NOTICE file. */
    noticeText?: string;
    /** Why we ship this dep (1-line context for trader/auditor reading Credits) */
    purpose: string;
}

export const OSS_NOTICES: OssNotice[] = [
    {
        name: 'lightweight-charts',
        license: 'Apache-2.0',
        copyright: 'Copyright 2023 TradingView, Inc.',
        homepage: 'https://github.com/tradingview/lightweight-charts',
        noticeText:
            'TradingView Lightweight Charts™\n' +
            'Copyright 2023 TradingView, Inc.\n' +
            'Licensed under the Apache License, Version 2.0.\n' +
            'https://www.tradingview.com/',
        purpose:
            'Canvas chart rendering primitive (candles, volume, crosshair). Default in-built ' +
            'attribution logo replaced by this Credits surface per Apache 2.0 §4(d) NOTICE clause.',
    },
    {
        name: 'svelte',
        license: 'MIT',
        copyright: 'Copyright (c) 2016–present Rich Harris and contributors',
        homepage: 'https://github.com/sveltejs/svelte',
        purpose: 'UI framework — reactive component model, runes ($state/$derived/$effect).',
    },
    {
        name: 'vite',
        license: 'MIT',
        copyright: 'Copyright (c) 2019-present, VoidZero Inc. and Vite contributors',
        homepage: 'https://github.com/vitejs/vite',
        purpose: 'Dev server + production bundler.',
    },
    {
        name: 'typescript',
        license: 'Apache-2.0',
        copyright: 'Copyright (c) Microsoft Corporation',
        homepage: 'https://github.com/microsoft/TypeScript',
        purpose: 'Type-safe authoring of UI source.',
    },
    {
        name: 'uuid',
        license: 'MIT',
        copyright: 'Copyright (c) 2010-2020 Robert Kieffer and other contributors',
        homepage: 'https://github.com/uuidjs/uuid',
        purpose: 'Stable IDs for client-side drawings (ADR-0007).',
    },
];
