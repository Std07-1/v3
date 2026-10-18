// src/chart/alerts/chartAlerts.ts
// ADR-0114 S8: зв'язка графіка з контролером ліній-сповіщень — дзвіночки на лініях і дія для фігури (меню стилю за
// id, капсула телефона — виділена). Окремий модуль, щоб ChartPane лише під'єднував (X33), а DrawingsRenderer
// лишився без знання про сповіщення (читає його лише `drawingsView()`).
//
// `refresh()` кличеться після кожного кадру рендерера і після кожної зміни стану контролера; назовні віддає лише
// зміни (`sameBells`, ключ дії), тож Svelte не перемальовує DOM щокадру.

import { get } from 'svelte/store';
import type { IChartApi, ISeriesApi, SeriesType } from 'lightweight-charts';
import { currentPair } from '../../app/frameRouter';
import type { DrawingsRenderer } from '../drawings/DrawingsRenderer';
import { alerts } from '../../lib/alerts/alertsStore';
import { alertActionFor, type AlertAction } from '../../lib/alerts/alertUi';
import { layoutBells, sameBells, type Bell } from './alertBells';

export interface ChartAlertsOutputs {
  /** Дзвіночки і ширина шкали ціни праворуч (дзвіночок стоїть біля неї). */
  bells(bells: Bell[], priceScaleWidth: number): void;
  /** Дія для виділеної фігури (капсула телефона); null — кнопки нема. */
  selection(action: AlertAction | null): void;
}

/** Рядок кнопок над правим краєм графіка (App.svelte); на телефоні ☰ стоїть упритул до шкали — колонка дзвіночків. */
const TOP_BUTTONS_SELECTOR = '.top-right-bar';

export class ChartAlertsBinding {
  private bells: Bell[] = [];
  private priceScaleWidth = -1;
  private selectionKey = '';
  /** null — ще не міряли; міряємо раз і після зміни розміру рядка кнопок чи вікна, не щокадру. */
  private topReserved: number | null = null;
  private topBarObserver: ResizeObserver | null = null;
  private readonly stopView: () => void;
  private readonly onResize = () => {
    this.topReserved = null;
    this.refresh();
  };

  constructor(
    private readonly renderer: DrawingsRenderer,
    private readonly chart: IChartApi,
    private readonly series: ISeriesApi<SeriesType>,
    private readonly container: HTMLElement,
    private readonly out: ChartAlertsOutputs,
  ) {
    this.stopView = alerts.view.subscribe(() => this.refresh());
    window.addEventListener('resize', this.onResize);
  }

  refresh(): void {
    const symbol = get(currentPair)?.symbol ?? null;
    const view = get(alerts.view);
    const { drawings, selectedId } = this.renderer.drawingsView();

    const paneHeight = this.container.clientHeight - this.chart.timeScale().height();
    const width = this.chart.priceScale('right').width();
    const bells =
      symbol === null || view.alerts.length === 0
        ? []
        : layoutBells({
            drawings,
            alerts: view.alerts,
            symbol,
            degraded: view.degraded,
            pushGone: view.pushGone,
            priceToY: (price) => this.series.priceToCoordinate(price),
            paneHeight,
            topReserved: this.measureTopReserved(),
          });
    if (!sameBells(bells, this.bells) || width !== this.priceScaleWidth) {
      this.bells = bells;
      this.priceScaleWidth = width;
      this.out.bells(bells, width);
    }

    const selected = selectedId === null ? null : (drawings.find((d) => d.id === selectedId) ?? null);
    const action = alertActionFor(selected, view, symbol);
    const key = action === null ? '' : `${selectedId}|${action.kind}`;
    if (key !== this.selectionKey) {
      this.selectionKey = key;
      this.out.selection(action);
    }
  }

  /** Дія для фігури за id (меню стилю) або для виділеної (капсула). Відмова вже гучна в контролері (`lastError`).
   *  Пуш без підписки: спершу дозвіл і підписка (у цьому ж кліку — до будь-якого мережевого await), далі — лінія. */
  async act(drawingId?: string): Promise<void> {
    const pair = get(currentPair);
    const { drawings, selectedId } = this.renderer.drawingsView();
    const drawing = drawings.find((d) => d.id === (drawingId ?? selectedId)) ?? null;
    const view = get(alerts.view);
    const action = alertActionFor(drawing, view, pair?.symbol ?? null);
    if (pair === null || drawing === null || action === null || action.hintOnly) return;
    if (action.kind === 'resubscribe' || (view.channel === 'push' && view.status === 'unpaired')) {
      try {
        await alerts.enablePush();
      } catch {
        return; // причина вже в lastError (спливне повідомлення)
      }
      if (action.kind === 'resubscribe') return;
    }
    if (action.kind === 'enable') await alerts.enable(drawing, pair.symbol, pair.tf);
    else if (action.kind === 'disable') await alerts.disable(drawing.id, pair.symbol);
    else await alerts.rearm(drawing, pair.symbol, pair.tf);
  }

  /** Дія для фігури меню стилю (правий клік): та сама логіка, що для виділеної. */
  actionFor(drawingId: string): AlertAction | null {
    const symbol = get(currentPair)?.symbol ?? null;
    const drawing = this.renderer.drawingsView().drawings.find((d) => d.id === drawingId) ?? null;
    return alertActionFor(drawing, get(alerts.view), symbol);
  }

  dispose(): void {
    this.stopView();
    window.removeEventListener('resize', this.onResize);
    this.topBarObserver?.disconnect();
  }

  private measureTopReserved(): number {
    if (this.topReserved === null) {
      const bar = document.querySelector(TOP_BUTTONS_SELECTOR);
      // Рядок міняє розмір і без зміни вікна (догрузився шрифт, з'явився чи зник пункт рядка)
      if (bar !== null && this.topBarObserver === null) {
        this.topBarObserver = new ResizeObserver(this.onResize);
        this.topBarObserver.observe(bar);
      }
      this.topReserved =
        bar === null ? 0 : Math.max(0, buttonsBottom(bar) - this.container.getBoundingClientRect().top);
    }
    return this.topReserved;
  }
}

/** Низ рядка разом із кнопками, що висять під ним: олівець ADR-0112 (`top: 100%`, у тій самій колонці). Випадне
 *  меню ☰ глибше за прямих нащадків — не рахується; приховані (`display: none`) дають 0. */
function buttonsBottom(bar: Element): number {
  let bottom = bar.getBoundingClientRect().bottom;
  for (const child of bar.children) bottom = Math.max(bottom, child.getBoundingClientRect().bottom);
  return bottom;
}
