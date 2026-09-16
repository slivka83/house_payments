from __future__ import annotations

import errno
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .stats import (
    FALLBACK_GROUP,
    GROUP_ORDER,
    VALUE_FIELDS,
    aggregate,
    canonical_service_group,
    charge_from_dict,
    collect_receipt_charges,
    find_supplier_receipt,
    month_range,
    pdf_support_available,
    supplier_label,
)
from .store import Store

BUILD = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

INDEX_HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>МосОблЕИРЦ — начисления</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
:root {
  color-scheme: light dark;
  --bg: #f4f6fa;
  --card: #ffffff;
  --text: #1c2333;
  --muted: #667085;
  --border: #e3e8ef;
  --accent: #2f6fed;
  --error: #d9534f;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12151c;
    --card: #1b202b;
    --text: #e6e9ef;
    --muted: #98a2b3;
    --border: #2a3140;
    --accent: #5b8cff;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px;
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
}
h1 { font-size: 20px; margin: 0; }
h2 { font-size: 16px; margin: 0 0 12px; }
.wrap { max-width: none; margin: 0; }
.title-select {
  font-size: 20px;
  font-weight: 700;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card);
  color: var(--text);
  cursor: pointer;
}
.topbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px 20px;
  margin-bottom: 8px;
}
.chart-layout {
  display: flex;
  gap: 24px;
  align-items: flex-start;
}
.legend-side { flex: 0 0 280px; max-width: 320px; }
.chart-area { flex: 1 1 auto; min-width: 0; }
@media (max-width: 900px) {
  .chart-layout { flex-direction: column; }
  .legend-side { flex: none; width: 100%; max-width: none; }
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: end;
  margin-bottom: 0;
}
.controls label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
select, button {
  font: inherit;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--text);
}
button { cursor: pointer; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
}
.icon-button svg { width: 18px; height: 18px; display: block; }
.icon-button.spinning svg { animation: icon-spin 0.8s linear infinite; }
@keyframes icon-spin { to { transform: rotate(360deg); } }
.notice.error {
  color: var(--error);
  border-color: color-mix(in srgb, var(--error) 30%, transparent);
  background: color-mix(in srgb, var(--error) 8%, transparent);
}
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 16px;
}
.chart-box { position: relative; height: 460px; min-height: 320px; }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 8px 10px; text-align: right; white-space: nowrap; }
.month-link { color: inherit; text-decoration: none; border-bottom: 1px dashed var(--muted); }
.month-link:hover { color: var(--accent); border-color: var(--accent); }
.service-supplier { display: block; font-size: 11px; color: var(--muted); font-weight: 400; }
th:first-child, td:first-child { text-align: left; }
thead th { color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--border); }
tbody tr:nth-child(even) { background: color-mix(in srgb, var(--border) 30%, transparent); }
tfoot td { font-weight: 700; border-top: 1px solid var(--border); }
.legend-toolbar {
  display: flex;
  justify-content: flex-start;
  margin-bottom: 8px;
}
.legend-toolbar button {
  padding: 5px 12px;
  font-size: 12px;
  color: var(--muted);
  background: transparent;
}
.legend-toolbar button:hover { color: var(--accent); border-color: var(--accent); }
.legend {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.legend-group { margin-bottom: 10px; }
.legend-group h4 {
  margin: 0 0 4px;
  font-size: 11px;
  color: var(--muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .04em;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 13px;
  cursor: pointer;
  user-select: none;
}
.legend-item .swatch { width: 12px; height: 12px; border-radius: 3px; flex: none; }
.legend-tip {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border: 1px solid var(--muted);
  border-radius: 50%;
  color: var(--muted);
  font-size: 10px;
  line-height: 1;
  font-weight: 600;
  cursor: help;
  flex: none;
}
.legend-tip:hover { color: var(--accent); border-color: var(--accent); }
.legend-tip::after {
  content: attr(data-tip);
  position: absolute;
  right: 0;
  bottom: calc(100% + 6px);
  width: max-content;
  max-width: 260px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--text);
  color: var(--card);
  font-size: 12px;
  font-weight: 400;
  line-height: 1.35;
  white-space: pre-line;
  text-align: left;
  opacity: 0;
  pointer-events: none;
  transition: opacity .12s;
  z-index: 20;
}
.legend-tip:hover::after { opacity: 1; }
.legend-item.off { opacity: .4; text-decoration: line-through; }
.legend-item:hover { color: var(--accent); }
.legend-item.active,
.legend-item.selected {
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  border-radius: 6px;
  font-weight: 600;
  padding-left: 8px;
  padding-right: 8px;
}
.notice {
  color: var(--muted);
  font-size: 13px;
  background: color-mix(in srgb, var(--accent) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 12px;
}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <select id="value" class="title-select" title="Показатель">
      <option value="charged">Начисления</option>
      <option value="volume">Объём</option>
    </select>
    <div class="controls">
      <select id="months">
        <option value="6">6 месяцев</option>
        <option value="12" selected>12 месяцев</option>
        <option value="18">18 месяцев</option>
        <option value="24">24 месяца</option>
        <option value="30">30 месяцев</option>
        <option value="36">36 месяцев</option>
      </select>
      <button id="reload" class="primary icon-button" type="button" title="Пересканировать папки" aria-label="Пересканировать папки">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21 12a9 9 0 1 1-2.64-6.36"/>
          <polyline points="21 3 21 9 15 9"/>
        </svg>
      </button>
    </div>
  </div>

  <div class="card">
    <div id="categoriesNotice" class="notice" hidden></div>
    <div class="chart-layout">
      <div class="chart-area">
        <div class="chart-box" id="categoriesChartBox"><canvas id="categoriesChart"></canvas></div>
      </div>
      <aside class="legend-side">
        <div class="legend-toolbar" id="legendToolbar">
          <button id="toggleAll" type="button">Скрыть все</button>
        </div>
        <div class="legend" id="legend"></div>
      </aside>
    </div>
  </div>
  <div class="card table-scroll">
    <table id="categoriesTable"></table>
  </div>
</div>
<script>
console.log('mosobleirc build __BUILD__');
const money = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const compact = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});
const PALETTE = ['#2f6fed', '#e0518a', '#f2a93b', '#2eb872', '#8a63d2', '#3aa8c1', '#d9534f', '#7cb342', '#b07aa1', '#667085', '#f06292', '#26a69a'];
const VALUE_LABELS = {charged: 'Начислено, ₽', volume: 'Объём'};
const MONTHS_RU = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const stackTotalsPlugin = {
  id: 'stackTotals',
  afterDatasetsDraw(chart) {
    if (chart.config.type !== 'bar') return;
    const {ctx} = chart;
    const color = getComputedStyle(document.documentElement).getPropertyValue('--text') || '#000';
    ctx.save();
    ctx.font = '600 12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif';
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    for (let index = 0; index < chart.data.labels.length; index += 1) {
      let total = 0;
      let top = Infinity;
      let x = null;
      chart.data.datasets.forEach((dataset, datasetIndex) => {
        const meta = chart.getDatasetMeta(datasetIndex);
        const element = meta.data[index];
        if (!element || meta.hidden) return;
        total += Number(dataset.data[index]) || 0;
        if (element.y < top) top = element.y;
        x = element.x;
      });
      if (!isFinite(top) || x === null || total === 0) continue;
      ctx.fillText(compact.format(total), x, top - 4);
    }
    ctx.restore();
  },
};
let categoriesChart = null;
let payload = null;
let chartColors = {};
let selectedService = null;
const hiddenServices = new Set();
const quantityFmt = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 4});

function monthTitle(items) {
  if (!items.length) return '';
  const month = (payload.categories.months || [])[items[0].dataIndex];
  return month ? formatMonth(month) : '';
}

function receiptUrl(month, supplier) {
  let url = '/api/receipt_pdf?month=' + encodeURIComponent(month);
  if (supplier) url += '&supplier=' + encodeURIComponent(supplier);
  return url;
}

function serviceSupplierKey(service) {
  const map = (payload && payload.categories && payload.categories.supplierKeys) || {};
  const keys = map[service] || [];
  return keys.length === 1 ? keys[0] : null;
}

function monthLink(month, service) {
  const supplier = service ? serviceSupplierKey(service) : null;
  return '<a class="month-link" href="' + receiptUrl(month, supplier) + '" target="_blank" rel="noopener" title="Открыть квитанцию">'
    + formatMonth(month) + '</a>';
}

function openReceipt(month, supplier) {
  if (!month) return;
  window.open(receiptUrl(month, supplier), '_blank', 'noopener');
}

function chartService(chart, element) {
  const value = (payload.categories && payload.categories.value) || 'charged';
  if (value === 'volume') return selectedService;
  if (chart.config.type === 'doughnut') return chart.data.labels[element.index] || null;
  const dataset = chart.data.datasets[element.datasetIndex];
  return (dataset && dataset.label) || null;
}

function monthFromChart(event, chart) {
  const months = (payload.categories && payload.categories.months) || [];
  if (!chart.scales || !chart.scales.x || event.x === undefined || event.x === null) return null;
  const value = chart.scales.x.getValueForPixel(event.x);
  if (value === undefined || value === null || Number.isNaN(value)) return null;
  const index = Math.max(0, Math.min(months.length - 1, Math.round(value)));
  return months[index] || null;
}

const chartReceiptOptions = {
  onHover: (event, elements, chart) => {
    const target = event.native && event.native.target;
    if (target) target.style.cursor = elements.length ? 'pointer' : 'default';
  },
  onClick: (event, elements, chart) => {
    const months = (payload.categories && payload.categories.months) || [];
    let month = null;
    let supplier = null;
    if (elements && elements.length) {
      month = months[elements[0].index] || null;
      const service = chartService(chart, elements[0]);
      if (service) supplier = serviceSupplierKey(service);
    }
    if (!month) month = monthFromChart(event, chart);
    openReceipt(month, supplier);
  },
};

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (char) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[char]));
}

const SERVICE_HINTS = {
  'СОДЕРЖАНИЕ ЖИЛОГО ПОМЕЩЕНИЯ': 'Плата управляющей компании за содержание общего имущества дома: уборка подъездов и придомовой территории, текущий ремонт, обслуживание лифтов, инженерных сетей и кровли, управление домом. Минимальный перечень работ — по постановлению Правительства РФ № 290; размер платы утверждает общее собрание собственников. Тариф — за 1 кв.м площади.',
  'ВЗНОС НА КАПИТАЛЬНЫЙ РЕМОНТ': 'Обязательный взнос собственника в фонд капитального ремонта (ЖК РФ, ст. 154). За счёт фонда ремонтируют крышу, фасад, лифты и инженерные сети. Минимальный размер взноса устанавливает регион; деньги собирает региональный оператор или спецсчёт дома. Платят собственники, а не наниматели.',
  'ЭЛЕКТРОСНАБЖЕНИЕ ДЕНЬ ОДН': 'Электроэнергия на содержание общего имущества по дневной зоне тарифа: освещение подъездов и двора, лифты, насосы, вентиляция. Считается по нормативу с перерасчётом по общедомовому счётчику (ОДПУ) и делится между квартирами пропорционально площади.',
  'ЭЛЕКТРОСНАБЖЕНИЕ НОЧЬ ОДН': 'Электроэнергия на содержание общего имущества по ночной (дешёвой) зоне тарифа. Считается по нормативу или общедомовому счётчику и распределяется между квартирами пропорционально площади.',
  'ЭЛЕКТРОЭНЕРГИЯ (Т1) ДЕНЬ': 'Электроэнергия в квартире по дневному тарифу (в Московской области обычно 7:00–23:00). Расход — разница показаний счётчика, объём умножается на дневной тариф; в счёте Мосэнергосбыта видны показания: старт → конец.',
  'ЭЛЕКТРОЭНЕРГИЯ (Т2) НОЧЬ': 'Электроэнергия в квартире по ночному тарифу (обычно 23:00–7:00) — он ниже дневного. Нужен двухтарифный счётчик; расход умножается на ночной тариф.',
  'ЖКУ (ИТОГ ПО КВИТАНЦИИ)': 'Итоговая сумма из квитанции, в которой нет расшифровки по услугам (только общая сумма к оплате). Показывается, если за этот месяц нет детальной квитанции от другого поставщика.',
  'ПОДОГРЕВ ВОДЫ ДЛЯ ГВС': 'Компонент «тепловая энергия» двухкомпонентного тарифа ГВС: сколько тепла (Гкал) потрачено на нагрев холодной воды до горячей. Оплачивается ресурсоснабжающей организации отдельно от самой воды — компонента «теплоноситель».',
  'ВОДООТВЕДЕНИЕ ОДН': 'Отведение сточных вод на содержание общего имущества (КР на СОИ): смывы при уборке подъездов, санузлы МОП. Входит в плату за содержание жилого помещения и распределяется между квартирами пропорционально площади.',
  'ХОЛОДНОЕ В/С ОДН': 'Холодная вода на содержание общего имущества (КР на СОИ, раньше — ОДН): уборка подъездов, санузлы МОП, полив газонов, промывка систем. Считается по нормативу с перерасчётом по общедомовому счётчику и делится между квартирами по площади.',
  'ОБРАЩЕНИЕ С ТКО': 'Вывоз, сортировка и переработка твёрдых коммунальных отходов. Коммунальная услуга с 2019 года; оказывает региональный оператор. В Московской области тариф считается по площади квартиры, в других регионах — по числу жильцов.',
  'ВОДООТВЕДЕНИЕ': 'Плата за отведение и очистку сточных вод: всё, что уходит из квартиры в канализацию. Объём обычно равен сумме холодной и горячей воды по счётчикам или нормативам. Это коммунальная услуга.',
  'ХОЛОДНОЕ В/С': 'Плата за холодную воду в квартиру: питьё, готовка, санузел. Считается по индивидуальному счётчику (ИПУ), а если его нет — по нормативу на человека. Вода на общедомовые нужды оплачивается отдельной строкой (КР на СОИ).',
  'ГОРЯЧЕЕ В/С (НОСИТЕЛЬ)': 'При двухкомпонентном тарифе ГВС эта строка — компонент «теплоноситель»: сама горячая вода (куб. м). Нагрев воды оплачивается отдельно как тепловая энергия (Гкал) — строка «Подогрев воды для ГВС». При одноставочном тарифе вся горячая вода идёт одной строкой.',
  'Отопление': 'Тепловая энергия (Гкал) на отопление квартиры. Платят равными долями 1/12 круглый год или только в отопительный сезон — зависит от региона и решения. В доме с общедомовым счётчиком объём распределяется между квартирами по площади.',
  'ТЕПЛОСНАБЖЕНИЕ': 'Тепловая энергия на отопление квартиры: от котельной или ТЭЦ. В большинстве домов Московской области платят равными долями круглый год (1/12); при расчёте по факту — только в отопительный сезон.',
  'ГАЗОСНАБЖЕНИЕ': 'Газ для плиты, водонагревателя или котла. Оплата — по счётчику, при его отсутствии — по нормативу на человека; тариф и норматив зависят от назначения газа (плита/котёл) и региона.',
  'ОХРАНА': 'Услуга охраны дома: пост, видеонаблюдение, пульт, обход территории. Дополнительная жилищная услуга; её вводят решением общего собрания собственников, размер платы утверждает собрание.',
  'ДОБРОВОЛЬНОЕ СТРАХОВАНИЕ': 'Добровольная страховка жилья от затопления, пожара и других аварий. Не обязательна: можно исключить из квитанции заявлением. В ЕПД её показывают отдельно — суммы «с учётом» и «без учёта добровольного страхования».',
  'УСЛУГИ КОНСЬЕРЖА': 'Работа консьержа: пропускной режим, порядок в подъезде, приём заявок жильцов. Дополнительная жилищная услуга, её вводят решением общего собрания; оплата — за месяц или с 1 кв.м площади.',
  'ДОМОФОН': 'Обслуживание домофона и подъездных замков: ремонт, замена трубок, связь, абонентское обслуживание. Относится к дополнительным жилищным услугам.',
};

const GROUP_HINTS = {
  'Жилищные услуги': 'Плата за жилое помещение (ЖК РФ, ст. 154): управление домом, содержание и текущий ремонт общего имущества, взнос на капремонт, а также дополнительные услуги по решению общего собрания (охрана, консьерж, домофон). Деньги получают управляющая компания, ТСЖ или подрядчики.',
  'Общедомовые нужды (КР на СОИ)': 'Коммунальные ресурсы (КР) на содержание общего имущества: вода, водоотведение и электроэнергия для подъездов, лифтов, освещения двора, полива. С 2017 года входят в плату за содержание жилого помещения, а не в коммунальные услуги квартиры. Считаются по нормативу с перерасчётом по общедомовому счётчику (ОДПУ).',
  'Коммунальные услуги': 'Ресурсы, которые потребляет квартира: холодная и горячая вода, водоотведение, электроэнергия, отопление, газ, обращение с ТКО. Считаются по счётчикам или нормативам; деньги идут ресурсоснабжающим организациям. Обращение с ТКО — коммунальная услуга с 2019 года.',
  'Иные услуги': 'Дополнительные и добровольные услуги, не входящие в содержание дома и коммунальные ресурсы: добровольное страхование, антенна, радиоточка. От страхования можно отказаться заявлением.',
  'Прочие услуги': 'Строки из платёжных документов, которые не удалось отнести к жилищным, коммунальным или иным услугам.',
  'Услуги': 'Услуги из вашего платёжного документа.',
};

function supplierNames(service) {
  const map = (payload && payload.categories && payload.categories.suppliers) || {};
  return map[service] || [];
}

function supplierNote(service) {
  const list = (payload && payload.categories && payload.categories.suppliersList) || [];
  if (list.length < 2) return '';
  const names = supplierNames(service);
  return names.length ? ' · ' + names.join(', ') : '';
}

function readingValue(value) {
  return value === null || value === undefined ? '—' : quantityFmt.format(value);
}

function readingNote(service, index) {
  const categories = (payload && payload.categories) || {};
  const month = (categories.months || [])[index];
  const reading = ((categories.readings || {})[service] || {})[month];
  if (!reading || (reading.start === null && reading.end === null)) return '';
  return ' (показания: ' + readingValue(reading.start) + ' → ' + readingValue(reading.end) + ')';
}

function serviceTip(service) {
  const names = supplierNames(service);
  const hint = serviceHint(service);
  return names.length ? hint + '\n\nПоставщик: ' + names.join(', ') : hint;
}

function serviceHint(service) {
  if (SERVICE_HINTS[service]) return SERVICE_HINTS[service];
  const upper = String(service).toUpperCase();
  const shared = /(^|[^А-ЯЁ])(ОДН|СОИ|КРСОИ|КР)([^А-ЯЁ]|$)/.test(upper) || upper.includes('ОБЩЕДОМ');
  if (shared) {
    if (upper.includes('ХОЛОДН')) return SERVICE_HINTS['ХОЛОДНОЕ В/С ОДН'];
    if (upper.includes('ГОРЯЧ')) return 'Горячая вода на содержание общего имущества: уборка подъездов, санузлы МОП. Считается по нормативу или общедомовому счётчику и делится между квартирами по площади.';
    if (upper.includes('ВОДООТВЕД')) return SERVICE_HINTS['ВОДООТВЕДЕНИЕ ОДН'];
    if (upper.includes('ЭЛЕКТРО')) return SERVICE_HINTS['ЭЛЕКТРОСНАБЖЕНИЕ ДЕНЬ ОДН'];
    return 'Коммунальный ресурс на содержание общего имущества (КР на СОИ): расход на подъезды, лифты и другие общие зоны. Входит в плату за содержание жилого помещения.';
  }
  if (upper.startsWith('ХОЛОДН')) return SERVICE_HINTS['ХОЛОДНОЕ В/С'];
  if (upper.startsWith('ГОРЯЧ')) return SERVICE_HINTS['ГОРЯЧЕЕ В/С (НОСИТЕЛЬ)'];
  if (upper.startsWith('ВОДООТВЕД')) return SERVICE_HINTS['ВОДООТВЕДЕНИЕ'];
  if (upper.startsWith('ПОДОГРЕВ')) return SERVICE_HINTS['ПОДОГРЕВ ВОДЫ ДЛЯ ГВС'];
  if (upper.startsWith('ЭЛЕКТРО')) return 'Платим за электроэнергию в квартире: свет, бытовая техника, готовка. Расход — по счётчику, тариф зависит от зоны суток.';
  if (upper.includes('КАПИТАЛЬН') || upper.includes('КАПРЕМОНТ')) return SERVICE_HINTS['ВЗНОС НА КАПИТАЛЬНЫЙ РЕМОНТ'];
  if (upper.includes('СОДЕРЖАНИЕ')) return SERVICE_HINTS['СОДЕРЖАНИЕ ЖИЛОГО ПОМЕЩЕНИЯ'];
  if (upper.includes('ТЕПЛ') || upper.includes('ОТОПЛ')) return SERVICE_HINTS['Отопление'];
  if (upper.includes('ГАЗ')) return SERVICE_HINTS['ГАЗОСНАБЖЕНИЕ'];
  if (upper.includes('ТКО') || upper.includes('ОТХОД')) return SERVICE_HINTS['ОБРАЩЕНИЕ С ТКО'];
  if (upper.includes('СТРАХОВАН')) return SERVICE_HINTS['ДОБРОВОЛЬНОЕ СТРАХОВАНИЕ'];
  if (upper.includes('КОНСЬЕРЖ')) return SERVICE_HINTS['УСЛУГИ КОНСЬЕРЖА'];
  if (upper.includes('ОХРАН')) return SERVICE_HINTS['ОХРАНА'];
  if (upper.includes('ДОМОФОН')) return SERVICE_HINTS['ДОМОФОН'];
  return 'Строка из вашей квитанции. Точную расшифровку смотрите в квитанции — кликните по месяцу на графике или в таблице.';
}

function groupHint(group) {
  return GROUP_HINTS[group] || GROUP_HINTS['Услуги'];
}

function tipHtml(text) {
  return '<span class="legend-tip" data-tip="' + escapeHtml(text) + '">?</span>';
}

function withAlpha(hex, alpha) {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!match) return hex;
  const value = parseInt(match[1], 16);
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return 'rgba(' + r + ', ' + g + ', ' + b + ', ' + alpha + ')';
}

const DIMMED_COLOR = 'rgba(158, 164, 178, 0.18)';

function themeTextColor() {
  const color = getComputedStyle(document.documentElement).getPropertyValue('--text');
  return (color || '#1c2333').trim();
}

function applyHighlight(service) {
  if (!categoriesChart || typeof Chart === 'undefined') return;
  const type = categoriesChart.config.type;
  const outline = themeTextColor();
  const active = [];
  if (type === 'bar') {
    categoriesChart.data.datasets.forEach((dataset, datasetIndex) => {
      const base = chartColors[dataset.label] || dataset.backgroundColor;
      const isTarget = service === null || dataset.label === service;
      dataset.backgroundColor = isTarget ? base : DIMMED_COLOR;
      if (service !== null && isTarget) {
        dataset.borderColor = withAlpha(outline, 0.75);
        dataset.borderWidth = 2;
        categoriesChart.data.labels.forEach((_, index) => active.push({datasetIndex, index}));
      } else {
        dataset.borderColor = 'rgba(0, 0, 0, 0)';
        dataset.borderWidth = 0;
      }
    });
  } else if (type === 'doughnut') {
    const dataset = categoriesChart.data.datasets[0];
    dataset.backgroundColor = categoriesChart.data.labels.map((label) => {
      const base = chartColors[label] || '#cccccc';
      return (service === null || label === service) ? base : DIMMED_COLOR;
    });
    dataset.borderColor = categoriesChart.data.labels.map((label) =>
      service !== null && label === service ? withAlpha(outline, 0.75) : 'rgba(0, 0, 0, 0)');
    dataset.borderWidth = 2;
    if (service !== null) {
      categoriesChart.data.labels.forEach((label, index) => {
        if (label === service) active.push({datasetIndex: 0, index});
      });
    }
  }
  if (typeof categoriesChart.setActiveElements === 'function') {
    categoriesChart.setActiveElements(active);
  }
  categoriesChart.update();
}

const valueSelect = document.getElementById('value');
const monthsSelect = document.getElementById('months');

function formatMonth(month) {
  const parsed = new Date(month + '-01T00:00:00');
  return MONTHS_RU[parsed.getMonth()] + ' ' + parsed.getFullYear();
}

function formatMonthAxis(month) {
  const parsed = new Date(month + '-01T00:00:00');
  return [MONTHS_RU[parsed.getMonth()], String(parsed.getFullYear()).slice(-2)];
}

function renderValue(amount) {
  return amount ? money.format(amount) : '—';
}

const dataCache = new Map();

function showError(message) {
  console.error(message);
  const notice = document.getElementById('categoriesNotice');
  if (!notice) return;
  notice.hidden = false;
  notice.classList.add('error');
  notice.textContent = 'Ошибка: ' + message;
}

function applyPayload(data) {
  payload = data;
  renderCategories();
}

async function loadData(force) {
  const key = monthsSelect.value + '|' + valueSelect.value;
  if (!force && dataCache.has(key)) {
    applyPayload(dataCache.get(key));
    return;
  }

  const params = new URLSearchParams({
    months: monthsSelect.value,
    value: valueSelect.value,
  });
  if (force) params.set('force', '1');
  try {
    const response = await fetch('/api/data?' + params.toString());
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || ('HTTP ' + response.status));
    dataCache.set(key, data);
    if (dataCache.size > 24) {
      dataCache.delete(dataCache.keys().next().value);
    }
    applyPayload(data);
  } catch (error) {
    showError(error.message);
  }
}

function renderCategories() {
  const categories = payload.categories;
  const notice = document.getElementById('categoriesNotice');
  const months = categories.months || [];

  if (!months.length) {
    notice.hidden = false;
    notice.textContent = categories.error
      || 'В БД нет разобранных платёжек за выбранный период. Положите PDF в data/receipts/<поставщик>/ и нажмите кнопку обновления (⟳).';
    if (categoriesChart) categoriesChart.destroy();
    categoriesChart = null;
    document.getElementById('categoriesTable').innerHTML = '';
    return;
  }

  notice.classList.remove('error');
  if (categories.error) {
    notice.hidden = false;
    notice.textContent = categories.error;
  } else if (categories.warning) {
    notice.hidden = false;
    notice.textContent = categories.warning;
  } else {
    notice.hidden = true;
  }

  const services = categories.services || {};
  const units = categories.units || {};
  const isVolume = categories.value === 'volume';
  const legendToolbar = document.getElementById('legendToolbar');
  if (legendToolbar) legendToolbar.style.display = isVolume ? 'none' : '';

  const orderedServices = (categories.order && categories.order.length ? categories.order : Object.keys(services))
    .filter((service) => service in services);
  const serviceEntries = orderedServices.map((service) => [service, services[service]]);
  const colors = {};
  serviceEntries.forEach(([service], index) => {
    colors[service] = PALETTE[index % PALETTE.length];
  });
  chartColors = colors;

  if (isVolume && (!selectedService || !(selectedService in services))) {
    selectedService = orderedServices[0] || null;
  }
  const visibleEntries = isVolume
    ? serviceEntries.filter(([service]) => service === selectedService)
    : serviceEntries.filter(([service]) => !hiddenServices.has(service));

  if (typeof Chart !== 'undefined') {
    if (categoriesChart) categoriesChart.destroy();
    if (isVolume) {
      const chargedMap = (categories.charged || {})[selectedService] || {};
      const volumeMap = (categories.volumes || {})[selectedService] || {};
      const unit = units[selectedService] || '';
      categoriesChart = new Chart(document.getElementById('categoriesChart'), {
        type: 'bar',
        data: {
          labels: months.map(formatMonthAxis),
          datasets: [
            {
              label: 'Начислено, ₽',
              data: months.map((month) => chargedMap[month] || 0),
              backgroundColor: '#2f6fed',
              borderRadius: 3,
              yAxisID: 'y',
            },
            {
              label: 'Объём' + (unit ? ', ' + unit : ''),
              data: months.map((month) => volumeMap[month] || 0),
              backgroundColor: '#2eb872',
              borderRadius: 3,
              yAxisID: 'y1',
            },
          ],
        },
        options: {
          ...chartReceiptOptions,
          responsive: true,
          maintainAspectRatio: false,
          interaction: {mode: 'nearest', intersect: true},
          scales: {
            x: {grid: {display: false}},
            y: {
              position: 'left',
              ticks: {callback: (value) => compact.format(value)},
              title: {display: true, text: 'Начислено, ₽'},
            },
            y1: {
              position: 'right',
              grid: {drawOnChartArea: false},
              ticks: {callback: (value) => quantityFmt.format(value)},
              title: {display: true, text: unit || 'Объём'},
            },
          },
          plugins: {
            legend: {display: false},
            tooltip: {
              callbacks: {
                title: monthTitle,
                label: (context) => {
                  if (context.dataset.yAxisID === 'y1') {
                    return context.dataset.label + ': ' + quantityFmt.format(context.parsed.y)
                      + readingNote(selectedService, context.dataIndex) + supplierNote(selectedService);
                  }
                  return context.dataset.label + ': ' + money.format(context.parsed.y) + ' ₽' + supplierNote(selectedService);
                },
              },
            },
          },
        },
      });
    } else if (months.length === 1) {
      const doughnutEntries = visibleEntries.slice().reverse();
      categoriesChart = new Chart(document.getElementById('categoriesChart'), {
        type: 'doughnut',
        data: {
          labels: doughnutEntries.map(([service]) => service),
          datasets: [{
            data: doughnutEntries.map(([, byMonth]) => byMonth[months[0]] || 0),
            backgroundColor: doughnutEntries.map(([service]) => colors[service]),
          }],
        },
        options: {
          ...chartReceiptOptions,
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: {display: false},
            tooltip: {callbacks: {label: (context) => context.label + ': ' + money.format(context.parsed) + ' ₽' + supplierNote(context.label)}},
          },
        },
      });
    } else {
      categoriesChart = new Chart(document.getElementById('categoriesChart'), {
        type: 'bar',
        data: {
          labels: months.map(formatMonthAxis),
          datasets: visibleEntries.map(([service, byMonth]) => ({
            label: service,
            data: months.map((month) => byMonth[month] || 0),
            backgroundColor: colors[service],
            stack: 'charges',
            borderRadius: 3,
          })),
        },
        plugins: [stackTotalsPlugin],
        options: {
          ...chartReceiptOptions,
          responsive: true,
          maintainAspectRatio: false,
          interaction: {mode: 'nearest', intersect: true},
          layout: {padding: {top: 20}},
          scales: {
            x: {stacked: true, grid: {display: false}},
            y: {stacked: true, ticks: {callback: (value) => compact.format(value)}, title: {display: true, text: VALUE_LABELS[categories.value] || categories.value}},
          },
          plugins: {
            legend: {display: false},
            tooltip: {
              callbacks: {
                title: monthTitle,
                label: (context) => context.dataset.label + ': ' + money.format(context.parsed.y) + ' ₽' + supplierNote(context.dataset.label),
              },
            },
          },
        },
      });
    }
  }

  renderLegend(services, colors, isVolume);

  const table = document.getElementById('categoriesTable');
  let html;
  if (isVolume) {
    const chargedMap = (categories.charged || {})[selectedService] || {};
    const volumeMap = (categories.volumes || {})[selectedService] || {};
    const readingsMap = (categories.readings || {})[selectedService] || {};
    const hasReadings = Object.keys(readingsMap).length > 0;
    const unit = units[selectedService] || '';
    let chargedSum = 0;
    let volumeSum = 0;
    html = '<thead><tr><th>Месяц</th><th>Начислено, ₽</th><th>Объём' + (unit ? ' (' + unit + ')' : '') + '</th>'
      + (hasReadings ? '<th>Показания</th>' : '') + '</tr></thead><tbody>';
    for (const month of months) {
      const charged = chargedMap[month] || 0;
      const volume = volumeMap[month] || 0;
      const reading = readingsMap[month];
      chargedSum += charged;
      volumeSum += volume;
      html += '<tr><td>' + monthLink(month, selectedService) + '</td><td>' + renderValue(charged) + '</td><td>' + (volume ? quantityFmt.format(volume) : '—') + '</td>'
        + (hasReadings ? '<td>' + (reading ? readingValue(reading.start) + ' → ' + readingValue(reading.end) : '—') + '</td>' : '')
        + '</tr>';
    }
    html += '</tbody><tfoot><tr><td>ИТОГО</td><td>' + renderValue(chargedSum) + '</td><td>' + (volumeSum ? quantityFmt.format(volumeSum) : '—') + '</td>'
      + (hasReadings ? '<td></td>' : '') + '</tr></tfoot>';
  } else {
    const tableEntries = orderedServices
      .slice()
      .reverse()
      .map((service) => [service, services[service]]);
    html = '<thead><tr><th>Услуга</th>';
    for (const month of months) html += '<th>' + monthLink(month) + '</th>';
    html += '<th>Итого</th></tr></thead><tbody>';
    for (const [service, byMonth] of tableEntries) {
      const sum = Object.values(byMonth).reduce((acc, value) => acc + value, 0);
      const names = supplierNames(service);
      const showSupplier = names.length > 1;
      html += '<tr><td>' + escapeHtml(service)
        + (showSupplier ? '<span class="service-supplier">' + escapeHtml(names.join(', ')) + '</span>' : '')
        + '</td>';
      for (const month of months) html += '<td>' + renderValue(byMonth[month]) + '</td>';
      html += '<td><b>' + renderValue(sum) + '</b></td></tr>';
    }
    html += '</tbody>';
    html += '<tfoot><tr><td>ИТОГО</td>';
    for (const month of months) html += '<td>' + renderValue(categories.totals[month]) + '</td>';
    html += '<td>' + renderValue(categories.grandTotal) + '</td></tr></tfoot>';
  }
  table.innerHTML = html;
  fitChart();
}

function fitChart() {
  const box = document.getElementById('categoriesChartBox');
  if (!box) return;
  const card = box.closest('.card');
  const top = box.getBoundingClientRect().top + window.scrollY;
  const bottomGap = 24 + (card ? parseFloat(getComputedStyle(card).paddingBottom) || 0 : 0);
  box.style.height = Math.max(320, window.innerHeight - top - bottomGap) + 'px';
}
window.addEventListener('resize', fitChart);

function renderLegend(services, colors, isVolume) {
  const box = document.getElementById('legend');
  if (!box) return;

  const categories = payload.categories || {};
  const groups = categories.groups && Object.keys(categories.groups).length
    ? categories.groups
    : {'Услуги': Object.keys(services)};

  let html = '';
  const groupEntries = Object.entries(groups).reverse();
  for (const [group, names] of groupEntries) {
    const items = names.filter((name) => name in services).reverse();
    if (!items.length) continue;
    html += '<div class="legend-group"><h4>' + escapeHtml(group) + tipHtml(groupHint(group)) + '</h4>';
    for (const service of items) {
      const state = isVolume
        ? (service === selectedService ? ' selected' : '')
        : (hiddenServices.has(service) ? ' off' : '');
      html += '<div class="legend-item' + state + '" data-service="' + encodeURIComponent(service) + '">'
        + '<span class="swatch" style="background:' + colors[service] + '"></span>'
        + '<span>' + escapeHtml(service) + '</span>'
        + tipHtml(serviceTip(service))
        + '</div>';
    }
    html += '</div>';
  }

  const toggle = document.getElementById('toggleAll');
  if (toggle) {
    const names = Object.keys(services);
    const allHidden = names.length > 0 && names.every((name) => hiddenServices.has(name));
    toggle.textContent = allHidden ? 'Показать все' : 'Скрыть все';
  }

  box.innerHTML = html;
  box.querySelectorAll('.legend-tip').forEach((tip) => {
    tip.addEventListener('click', (event) => event.stopPropagation());
  });
  box.querySelectorAll('.legend-item').forEach((element) => {
    const service = decodeURIComponent(element.dataset.service);
    element.addEventListener('click', () => {
      if (isVolume) {
        selectedService = service;
      } else if (hiddenServices.has(service)) {
        hiddenServices.delete(service);
      } else {
        hiddenServices.add(service);
      }
      renderCategories();
    });
    if (!isVolume) {
      element.addEventListener('mouseenter', () => {
        box.querySelectorAll('.legend-item.active').forEach((item) => item.classList.remove('active'));
        element.classList.add('active');
        if (!hiddenServices.has(service)) applyHighlight(service);
      });
      element.addEventListener('mouseleave', () => {
        element.classList.remove('active');
        applyHighlight(null);
      });
    }
  });
}

const reloadButton = document.getElementById('reload');
reloadButton.addEventListener('click', () => {
  reloadButton.classList.add('spinning');
  loadData(true).finally(() => reloadButton.classList.remove('spinning'));
});
document.getElementById('toggleAll').addEventListener('click', () => {
  const services = Object.keys((payload && payload.categories && payload.categories.services) || {});
  if (!services.length) return;
  const allHidden = services.every((service) => hiddenServices.has(service));
  services.forEach((service) => {
    if (allHidden) {
      hiddenServices.delete(service);
    } else {
      hiddenServices.add(service);
    }
  });
  renderCategories();
});
valueSelect.addEventListener('change', () => loadData(false));
monthsSelect.addEventListener('change', () => loadData(false));

(async () => {
  try {
    await loadData(false);
  } catch (error) {
    showError(error.message);
  }
})();
</script>
</body>
</html>
"""


@dataclass
class WebConfig:
    months: int = 12
    value: str = "charged"
    end_month: str | None = None
    db_path: str | None = "data/mosobleirc.sqlite"
    receipts_dir: str | None = "data/receipts"
    host: str = "0.0.0.0"
    port: int = 8765


class State:
    def __init__(self, config: WebConfig):
        self.config = config
        self.lock = threading.RLock()
        self._data_cache: dict = {}
        self.store = Store(config.db_path)
        if config.receipts_dir:
            try:
                Path(config.receipts_dir).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    def receipt_pdf(self, month: str, supplier: str | None = None) -> bytes:
        with self.lock:
            path = find_supplier_receipt(self.config.receipts_dir, month, supplier=supplier)
            if not path and supplier:
                path = find_supplier_receipt(self.config.receipts_dir, month)
            if not path:
                raise FileNotFoundError(f"Квитанция за {month} не найдена в папках поставщиков")
            return path.read_bytes()

    def data(
        self,
        *,
        months: int | None,
        value: str | None,
        force: bool = False,
    ) -> dict:
        months = months or self.config.months
        value = value or self.config.value
        if value not in VALUE_FIELDS:
            raise ValueError(f"Неизвестный показатель: {value}")
        months = max(1, min(months, 60))

        def log(supplier, month, note):
            print(f"[web] {supplier_label(supplier)}: {month} {note}", file=sys.stderr)

        with self.lock:
            cache_key = (self.config.end_month, months, value)
            if not force:
                cached_payload = self._data_cache.get(cache_key)
                if cached_payload is not None:
                    return {
                        **cached_payload,
                        "fetch": {"cacheHit": True, "parsed": 0, "cached": 0},
                    }

            report: dict = {}
            receipts_error: str | None = None
            parsed_now = 0
            warnings: list[str] | None = None
            refresh = force

            if not refresh:
                # обычная загрузка страницы — только то, что уже разобрано в БД
                category_rows = [
                    charge_from_dict(item) for item in self.store.load_all_receipt_charges()
                ]
            elif not pdf_support_available():
                receipts_error = (
                    "PDF не разбираются: установите зависимости — pip install -r requirements.txt"
                )
                print(f"[web] {receipts_error}", file=sys.stderr)
                category_rows = [
                    charge_from_dict(item) for item in self.store.load_all_receipt_charges()
                ]
            else:
                category_rows = collect_receipt_charges(
                    self.config.receipts_dir,
                    None,
                    store=self.store,
                    log=log,
                    report=report,
                )
                parsed_now = report.get("parsed", 0)
                if self.store.enabled:
                    pruned = self.store.prune_receipts(set(report.get("files", [])))
                    if pruned:
                        print(f"[web] из БД удалено устаревших разборов: {pruned}", file=sys.stderr)

            if self.store.enabled:
                warnings = [
                    f"{supplier_label(item['supplier'])}: {Path(item['path']).name} — {item['note']}"
                    for item in self.store.load_warnings()
                ]
            elif warnings is None:
                warnings = report.get("warnings") or []

            if self.config.end_month:
                month_list = month_range(self.config.end_month, months)
                wanted = set(month_list)
            else:
                available = sorted({row.month for row in category_rows if row.month})
                month_list = available[-months:]
                wanted = set(month_list)
            category_rows = [row for row in category_rows if row.month in wanted]

        months_cat, services, totals_cat, grand_total = aggregate(category_rows, value)

        _, charged_services, _, _ = aggregate(category_rows, "charged")
        _, volume_services, _, _ = aggregate(category_rows, "volume")

        service_groups: dict[str, str] = {}
        service_suppliers: dict[str, list[str]] = {}
        service_suppliers_raw: dict[str, list[str]] = {}
        for row in category_rows:
            # группу определяем по названию услуги, а не по разделу конкретной платёжки:
            # так одна и та же услуга у разных поставщиков попадает в один раздел
            service_groups.setdefault(
                row.service,
                canonical_service_group(row.service, row.group or FALLBACK_GROUP),
            )
            label = supplier_label(row.supplier)
            names = service_suppliers.setdefault(row.service, [])
            if label and label not in names:
                names.append(label)
            keys = service_suppliers_raw.setdefault(row.service, [])
            if row.supplier and row.supplier not in keys:
                keys.append(row.supplier)
        groups: dict[str, list[str]] = {}
        for group_name in GROUP_ORDER:
            names = [
                service
                for service in services
                if service_groups.get(service, FALLBACK_GROUP) == group_name
            ]
            if names:
                groups[group_name] = names
        for group_name in service_groups.values():
            if group_name in groups:
                continue
            names = [
                service
                for service in services
                if service_groups.get(service, FALLBACK_GROUP) == group_name
            ]
            if names:
                groups[group_name] = names
        order = [service for names in groups.values() for service in names]

        suppliers_list = sorted(
            {name for names in service_suppliers.values() for name in names}
        )

        service_readings: dict[str, dict[str, dict]] = {}
        for row in category_rows:
            if row.reading_start is None and row.reading_end is None:
                continue
            service_readings.setdefault(row.service, {})[row.month] = {
                "start": row.reading_start,
                "end": row.reading_end,
            }

        warning: str | None = None
        if warnings:
            shown = warnings[:3]
            warning = "Не все платёжки удалось разобрать: " + "; ".join(shown)
            if len(warnings) > len(shown):
                warning += f"; и ещё {len(warnings) - len(shown)}"

        payload = {
            "months": month_list,
            "categories": {
                "months": months_cat,
                "services": services,
                "charged": charged_services,
                "volumes": volume_services,
                "groups": groups,
                "order": order,
                "totals": totals_cat,
                "grandTotal": grand_total,
                "units": {row.service: row.unit for row in category_rows if row.unit},
                "suppliers": service_suppliers,
                "supplierKeys": service_suppliers_raw,
                "suppliersList": suppliers_list,
                "readings": service_readings,
                "value": value,
                "source": "receipts",
                "error": receipts_error,
                "warning": warning,
            },
            "rows": len(category_rows),
            "fetch": {
                "cacheHit": False,
                "parsed": parsed_now,
                "cached": report.get("cached", 0),
                "source": "folders" if refresh else "db",
            },
        }

        with self.lock:
            self._data_cache[cache_key] = payload

        return payload


class Handler(BaseHTTPRequestHandler):
    state: State
    server_version = "mosobleirc-web"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[web] {self.address_string()} {fmt % args}\n")

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        try:
            if parsed.path == "/":
                html = INDEX_HTML.replace("__BUILD__", BUILD)
                self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")
                return

            if parsed.path == "/api/data":
                months_value = query.get("months", [None])[0]
                self._send_json(
                    self.state.data(
                        months=int(months_value) if months_value else None,
                        value=query.get("value", [None])[0] or None,
                        force=query.get("force", ["0"])[0] == "1",
                    )
                )
                return

            if parsed.path == "/api/receipt_pdf":
                month = (query.get("month", [""])[0] or "").strip()
                if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                    raise ValueError("Некорректный месяц, ожидается YYYY-MM")
                supplier = (query.get("supplier", [""])[0] or "").strip()
                data = self.state.receipt_pdf(month, supplier or None)
                self._send_bytes(
                    data,
                    "application/pdf",
                    200,
                    {"Content-Disposition": f'inline; filename="{month}.pdf"'},
                )
                return

            self._send_json({"message": "not found"}, 404)
        except FileNotFoundError as error:
            print(f"[web] ошибка: {error}", file=sys.stderr)
            self._send_json({"message": str(error)}, 404)
        except ValueError as error:
            print(f"[web] ошибка: {error}", file=sys.stderr)
            self._send_json({"message": str(error)}, 400)
        except Exception as error:
            print(f"[web] неожиданная ошибка: {type(error).__name__}: {error}", file=sys.stderr)
            self._send_json({"message": f"{type(error).__name__}: {error}"}, 500)


def primary_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def project_snapshot() -> dict[str, int]:
    root = Path(__file__).resolve().parent
    snapshot: dict[str, int] = {}
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            snapshot[str(path)] = path.stat().st_mtime_ns
        except OSError:
            continue
    dotenv = Path.cwd() / ".env"
    if dotenv.exists():
        try:
            snapshot[str(dotenv)] = dotenv.stat().st_mtime_ns
        except OSError:
            pass
    return snapshot


def _stop_child(child) -> None:
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def run_with_reloader() -> None:
    env = dict(os.environ)
    env["MOSOBLEIRC_RELOAD_CHILD"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    command = [sys.executable, "-m", "mosobleirc", *sys.argv[1:]]

    def start():
        return subprocess.Popen(command, env=env)

    def handle_signal(signum, frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, handle_signal)
    except (ValueError, OSError):
        pass

    print("Автоперезапуск включён: после правок кода сервер перезапустится сам, страницу достаточно обновить.", file=sys.stderr)
    child = start()
    snapshot = project_snapshot()

    try:
        while True:
            time.sleep(1.0)
            current = project_snapshot()
            if current != snapshot:
                print("[reload] код изменился — перезапускаю сервер…", file=sys.stderr)
                _stop_child(child)
                child = start()
                snapshot = current
            elif child.poll() is not None:
                print(f"[reload] сервер остановился (код {child.returncode}), перезапуск через 3 с…", file=sys.stderr)
                time.sleep(3)
                child = start()
                snapshot = project_snapshot()
    except KeyboardInterrupt:
        print("Остановлено", file=sys.stderr)
    finally:
        _stop_child(child)


def run_server(config: WebConfig) -> None:
    reload_enabled = (
        os.environ.get("MOSOBLEIRC_RELOAD", "1") != "0"
        and os.environ.get("MOSOBLEIRC_RELOAD_CHILD") != "1"
    )
    if reload_enabled and "web" in sys.argv[1:]:
        run_with_reloader()
        return

    state = State(config)
    handler = type("BoundHandler", (Handler,), {"state": state})
    try:
        server = ThreadingHTTPServer((config.host, config.port), handler)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            print(
                f"Порт {config.port} уже занят. Задай другой: MOSOBLEIRC_PORT=8766 "
                f"или python -m mosobleirc web --port 8766",
                file=sys.stderr,
            )
        else:
            print(f"Не удалось запустить сервер на {config.host}:{config.port}: {error}", file=sys.stderr)
        raise SystemExit(1)

    print(f"Веб-сервер запущен на {config.host}:{config.port}", file=sys.stderr)
    print("Открой в браузере Windows:", file=sys.stderr)
    print(f"  http://localhost:{config.port}/", file=sys.stderr)
    print(f"  http://127.0.0.1:{config.port}/", file=sys.stderr)
    ip = primary_ip()
    if ip and ip not in ("127.0.0.1",):
        print(f"  http://{ip}:{config.port}/   (IP этой машины)", file=sys.stderr)
    if config.db_path:
        print(f"Кэш: {Path(config.db_path).resolve()} (SQLite)", file=sys.stderr)
    if config.receipts_dir:
        receipts_root = Path(config.receipts_dir).resolve()
        print(f"Платёжки: {receipts_root}", file=sys.stderr)
        print(
            f"  папки поставщиков: {receipts_root}/<поставщик>/... (PDF, имя вида ГГГГ-ММ.pdf)",
            file=sys.stderr,
        )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Остановлено", file=sys.stderr)
    finally:
        server.server_close()
        state.store.close()
