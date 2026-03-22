<!--
  ADR-0031: Bias Banner — multi-TF trend bias display.
  Показує D1↑ H4↑ H1↓ M15↑ pills з кольоровим кодуванням.
  Read-only overlay, no side effects.
-->
<script lang="ts">
    // TF labels ordered high→low (display order per ADR-0031)
    const TF_LABELS: Record<string, string> = {
        "86400": "D1",
        "14400": "H4",
        "3600": "H1",
        "900": "M15",
    };
    const TF_ORDER = ["86400", "14400", "3600", "900"];

    interface Props {
        biasMap: Record<string, string>;
        momentumMap?: Record<string, { b: number; r: number }>;
        inline?: boolean;
    }
    let { biasMap, momentumMap = {}, inline = false }: Props = $props();

    // Momentum → directional dots: count → dots, color by dominant side
    function momInfo(m: { b: number; r: number } | undefined): {
        dots: string;
        cls: string;
    } {
        if (!m) return { dots: "", cls: "" };
        const max = Math.max(m.b, m.r);
        if (max <= 0) return { dots: "", cls: "" };
        const dots = max <= 2 ? "·" : max <= 5 ? "··" : "···";
        const cls =
            m.b > m.r ? "bull-mom" : m.r > m.b ? "bear-mom" : "neutral-mom";
        return { dots, cls };
    }

    // Derived: pills тільки для відомих TF з bias_map
    let pills = $derived(
        TF_ORDER.filter((k) => biasMap[k] != null).map((k) => {
            const mi = momInfo(momentumMap[k]);
            return {
                label: TF_LABELS[k],
                bias: biasMap[k] as "bullish" | "bearish",
                arrow: biasMap[k] === "bullish" ? "▲" : "▼",
                momDots: mi.dots,
                momCls: mi.cls,
            };
        }),
    );

    // Alignment: всі активні TF мають однаковий bias → підсвітка
    let aligned = $derived(
        pills.length >= 2 && pills.every((p) => p.bias === pills[0].bias),
    );
</script>

{#if pills.length > 0}
    <div class="bias-banner" class:aligned class:is-inline={inline}>
        {#each pills as p (p.label)}
            <span
                class="bias-pill"
                class:bull={p.bias === "bullish"}
                class:bear={p.bias === "bearish"}
            >
                {p.label}<span class="arrow">{p.arrow}</span
                >{#if p.momDots}<span class="mom {p.momCls}">{p.momDots}</span
                    >{/if}
            </span>
        {/each}
    </div>
{/if}

<style>
    .bias-banner {
        position: absolute;
        top: 72px;
        left: 10px;
        z-index: 30;
        display: flex;
        flex-direction: row;
        gap: 3px;
        pointer-events: none;
    }
    .bias-banner.is-inline {
        position: static;
        top: unset;
        left: unset;
        z-index: unset;
    }
    .bias-banner.aligned {
        outline: 1px solid rgba(46, 204, 113, 0.35);
        outline-offset: 2px;
        border-radius: 4px;
    }
    .bias-pill {
        font-size: 9px;
        font-weight: 600;
        padding: 1px 4px;
        border-radius: 3px;
        backdrop-filter: blur(4px);
        line-height: 1.3;
        letter-spacing: 0.3px;
        display: inline-flex;
        align-items: center;
        gap: 1px;
    }
    .bias-pill.bull {
        color: #2ecc71;
        background: rgba(46, 204, 113, 0.1);
        border: 1px solid rgba(46, 204, 113, 0.3);
    }
    .bias-pill.bear {
        color: #ef5350;
        background: rgba(239, 83, 80, 0.1);
        border: 1px solid rgba(239, 83, 80, 0.3);
    }
    .arrow {
        font-size: 7px;
        margin-left: 1px;
    }
    .mom {
        font-size: 8px;
        margin-left: 2px;
        letter-spacing: 0px;
    }
    .mom.bull-mom {
        color: #2ecc71;
        opacity: 0.9;
    }
    .mom.bear-mom {
        color: #ef5350;
        opacity: 0.9;
    }
    .mom.neutral-mom {
        color: #787b86;
        opacity: 0.65;
    }
</style>
