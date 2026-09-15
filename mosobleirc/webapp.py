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
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .client import MosOblEIRCClient, MosOblEIRCError
from .stats import (
    VALUE_FIELDS,
    ReceiptCache,
    aggregate,
    collect_charges,
    collect_receipt_charges,
    has_category_history,
    month_range,
    pdf_support_available,
    previous_month,
)
from .tokens import load_token, save_token

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
.chart-box { position: relative; height: 460px; }
.chart-box.small { height: 320px; }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 8px 10px; text-align: right; white-space: nowrap; }
.month-link { color: inherit; text-decoration: none; border-bottom: 1px dashed var(--muted); }
.month-link:hover { color: var(--accent); border-color: var(--accent); }
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
  white-space: normal;
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
        <option value="24">24 месяца</option>
        <option value="36">36 месяцев</option>
      </select>
      <button id="reload" class="primary icon-button" type="button" title="Обновить из ЛКК" aria-label="Обновить из ЛКК">
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

function receiptUrl(month) {
  return '/api/receipt_pdf?month=' + encodeURIComponent(month);
}

function monthLink(month) {
  return '<a class="month-link" href="' + receiptUrl(month) + '" target="_blank" rel="noopener" title="Открыть квитанцию">'
    + formatMonth(month) + '</a>';
}

function openReceipt(month) {
  if (!month) return;
  window.open(receiptUrl(month), '_blank', 'noopener');
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
    if (elements && elements.length) {
      month = months[elements[0].index] || null;
    }
    if (!month) month = monthFromChart(event, chart);
    openReceipt(month);
  },
};

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (char) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[char]));
}

const SERVICE_HINTS = {
  'СОДЕРЖАНИЕ ЖИЛОГО ПОМЕЩЕНИЯ': 'Платим управляющей компании за дом: уборка и ремонт подъездов, обслуживание лифтов, сантехники, электрики и кровли, содержание придомовой территории, текущий ремонт.',
  'ВЗНОС НА КАПИТАЛЬНЫЙ РЕМОНТ': 'Накопительный взнос в фонд капремонта: за эти деньги через годы ремонтируют крышу, фасад, лифты и инженерные сети дома. Размер взноса устанавливает регион, а не УК.',
  'ЭЛЕКТРОСНАБЖЕНИЕ ДЕНЬ ОДН': 'Электроэнергия на общие зоны дома — освещение подъездов и двора, лифты, насосы — по дневному тарифу. Расход делится между квартирами пропорционально площади.',
  'ЭЛЕКТРОСНАБЖЕНИЕ НОЧЬ ОДН': 'Электроэнергия на общие зоны дома — освещение подъездов и двора, лифты, насосы — по ночному (дешёвому) тарифу. Делится между квартирами пропорционально площади.',
  'ВОДООТВЕДЕНИЕ ОДН': 'Отведение сточных вод из мест общего пользования (уборка подъездов, санузлы МОП). Сумма делится между квартирами пропорционально площади.',
  'ХОЛОДНОЕ В/С ОДН': 'Холодная вода для общих зон дома: уборка подъездов, полив газонов. Расход делится между квартирами пропорционально площади.',
  'ОБРАЩЕНИЕ С ТКО': 'Платим региональному оператору за вывоз, сортировку и переработку мусора. Тариф считается по площади квартиры, а не по числу жильцов.',
  'ВОДООТВЕДЕНИЕ': 'Платим за приём и очистку сточных вод, которые уходят из квартиры в канализацию. Считается по суммарному водопотреблению (или счётчику).',
  'ХОЛОДНОЕ В/С': 'Платим поставщику за холодную воду в квартиру: питьё, готовка, санузел. Расход — по счётчику, без счётчика — по нормативу на человека.',
  'ГОРЯЧЕЕ В/С (НОСИТЕЛЬ)': 'Платим за горячую воду: саму воду и тепло, которое её нагревает. Расход — по счётчику или нормативу; деньги идут ресурсоснабжающей организации.',
  'ТЕПЛОСНАБЖЕНИЕ': 'Отопление квартиры: тепло от котельной или ТЭЦ. В Московской области платится равными долями круглый год.',
  'ГАЗОСНАБЖЕНИЕ': 'Газ для плиты и/или газового котла. Оплата — по счётчику или по нормативу на человека.',
  'ОХРАНА': 'Платим за охрану дома: пульт, видеонаблюдение, обход территории. Услуга появляется по решению общего собрания жильцов.',
  'ДОБРОВОЛЬНОЕ СТРАХОВАНИЕ': 'Добровольная страховка жилья от затопления, пожара и т.п. Не обязательна: можно отказаться, исключив строку из квитанции.',
  'УСЛУГИ КОНСЬЕРЖА': 'Платим за работу консьержа: пропускной режим, порядок в подъезде, приём заявок жильцов.',
  'ДОМОФОН': 'Обслуживание домофона и подъездных замков: ремонт, замена трубок, связь.',
};

const GROUP_HINTS = {
  'Жилищные услуги': 'Платим за сам дом и общее имущество: содержание и текущий ремонт, капремонт и ресурсы на общие зоны (ОДН). Деньги получают управляющая компания и подрядчики.',
  'Коммунальные услуги': 'Платим за ресурсы, которые потребляет квартира: вода, водоотведение, электроэнергия, тепло, вывоз мусора. Считается по счётчикам или нормативам, деньги идут ресурсоснабжающим организациям.',
  'Иные услуги': 'Дополнительные услуги по решению жильцов или включённые в квитанцию: охрана, консьерж, добровольное страхование.',
  'Услуги': 'Услуги из вашего платёжного документа.',
};

function serviceHint(service) {
  if (SERVICE_HINTS[service]) return SERVICE_HINTS[service];
  const upper = String(service).toUpperCase();
  if (upper.includes('ОДН')) {
    if (upper.includes('ХОЛОДН')) return SERVICE_HINTS['ХОЛОДНОЕ В/С ОДН'];
    if (upper.includes('ГОРЯЧ')) return 'Горячая вода для общих зон дома: уборка подъездов, санузлы МОП. Расход делится между квартирами пропорционально площади.';
    if (upper.includes('ВОДООТВЕД')) return SERVICE_HINTS['ВОДООТВЕДЕНИЕ ОДН'];
    if (upper.includes('ЭЛЕКТРО')) return SERVICE_HINTS['ЭЛЕКТРОСНАБЖЕНИЕ ДЕНЬ ОДН'];
    return 'Ресурс на общедомовые нужды: расход на подъезды, лифты и другие общие зоны. Делится между квартирами пропорционально площади.';
  }
  if (upper.startsWith('ХОЛОДН')) return SERVICE_HINTS['ХОЛОДНОЕ В/С'];
  if (upper.startsWith('ГОРЯЧ')) return SERVICE_HINTS['ГОРЯЧЕЕ В/С (НОСИТЕЛЬ)'];
  if (upper.startsWith('ВОДООТВЕД')) return SERVICE_HINTS['ВОДООТВЕДЕНИЕ'];
  if (upper.startsWith('ЭЛЕКТРО')) return 'Платим за электроэнергию в квартире: свет, бытовая техника, готовка. Расход — по счётчику, тариф зависит от зоны суток.';
  if (upper.includes('КАПИТАЛЬН')) return SERVICE_HINTS['ВЗНОС НА КАПИТАЛЬНЫЙ РЕМОНТ'];
  if (upper.includes('СОДЕРЖАНИЕ')) return SERVICE_HINTS['СОДЕРЖАНИЕ ЖИЛОГО ПОМЕЩЕНИЯ'];
  if (upper.includes('ТЕПЛ') || upper.includes('ОТОПЛ')) return SERVICE_HINTS['ТЕПЛОСНАБЖЕНИЕ'];
  if (upper.includes('ГАЗ')) return SERVICE_HINTS['ГАЗОСНАБЖЕНИЕ'];
  return 'Строка из вашей квитанции. Точную расшифровку смотрите в ЕПД — кликните по месяцу на графике или в таблице.';
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
    notice.textContent = categories.error || 'ЛКК не вернул разбивку по услугам.';
    if (categoriesChart) categoriesChart.destroy();
    categoriesChart = null;
    document.getElementById('categoriesTable').innerHTML = '';
    return;
  }

  notice.classList.remove('error');
  if (categories.error) {
    notice.hidden = false;
    notice.textContent = categories.error;
  } else if (categories.source === 'receipts' || categories.history) {
    notice.hidden = true;
  } else {
    notice.hidden = false;
    notice.textContent = 'ЛКК отдаёт разбивку по услугам только за текущий закрытый период — показан ' + formatMonth(months[0]) + '. Квитанций ЕПД за другие месяцы ЛКК не вернул.';
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
                    return context.dataset.label + ': ' + quantityFmt.format(context.parsed.y);
                  }
                  return context.dataset.label + ': ' + money.format(context.parsed.y) + ' ₽';
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
            tooltip: {callbacks: {label: (context) => context.label + ': ' + money.format(context.parsed) + ' ₽'}},
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
                label: (context) => context.dataset.label + ': ' + money.format(context.parsed.y) + ' ₽',
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
    const unit = units[selectedService] || '';
    let chargedSum = 0;
    let volumeSum = 0;
    html = '<thead><tr><th>Месяц</th><th>Начислено, ₽</th><th>Объём' + (unit ? ' (' + unit + ')' : '') + '</th></tr></thead><tbody>';
    for (const month of months) {
      const charged = chargedMap[month] || 0;
      const volume = volumeMap[month] || 0;
      chargedSum += charged;
      volumeSum += volume;
      html += '<tr><td>' + monthLink(month) + '</td><td>' + renderValue(charged) + '</td><td>' + (volume ? quantityFmt.format(volume) : '—') + '</td></tr>';
    }
    html += '</tbody><tfoot><tr><td>ИТОГО</td><td>' + renderValue(chargedSum) + '</td><td>' + (volumeSum ? quantityFmt.format(volumeSum) : '—') + '</td></tr></tfoot>';
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
      html += '<tr><td>' + service + '</td>';
      for (const month of months) html += '<td>' + renderValue(byMonth[month]) + '</td>';
      html += '<td><b>' + renderValue(sum) + '</b></td></tr>';
    }
    html += '</tbody>';
    html += '<tfoot><tr><td>ИТОГО</td>';
    for (const month of months) html += '<td>' + renderValue(categories.totals[month]) + '</td>';
    html += '<td>' + renderValue(categories.grandTotal) + '</td></tr></tfoot>';
  }
  table.innerHTML = html;
}

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
        + tipHtml(serviceHint(service))
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
    phone: str | None = None
    password: str | None = None
    token: str | None = None
    token_file: str | None = None
    account_filter: list[str] = field(default_factory=list)
    months: int = 12
    shift: int = 1
    anchor_day: int = 15
    value: str = "charged"
    end_month: str | None = None
    cache_dir: str | None = "data/raw"
    receipts_dir: str | None = "data/receipts"
    host: str = "0.0.0.0"
    port: int = 8765


def prompt_code(factor: str) -> str:
    if not sys.stdin.isatty():
        raise MosOblEIRCError(
            f"Нужен код второго фактора ({factor}), но ввод недоступен. Задайте MOSOBLEIRC_TOKEN"
        )
    return input(f"Код подтверждения ({factor}): ").strip()


class State:
    def __init__(self, config: WebConfig):
        self.config = config
        self.lock = threading.RLock()
        self._accounts: list[dict] | None = None
        self._data_cache: dict = {}
        self.token_file = str(Path(config.token_file).expanduser()) if config.token_file else None
        self.client = MosOblEIRCClient(
            phone=config.phone,
            password=config.password,
            token=config.token or load_token(self.token_file, config.phone),
            prompt_code=prompt_code,
        )

    def _ensure_login(self) -> None:
        if self.client.token:
            return
        print("[web] вход в ЛКК…", file=sys.stderr)
        token = self.client.login()
        print("[web] вход выполнен", file=sys.stderr)
        save_token(self.token_file, self.config.phone, token)

    def accounts(self, refresh: bool = False) -> list[dict]:
        with self.lock:
            if self._accounts is None or refresh:
                self._ensure_login()
                self._accounts = self.client.accounts()
            return self._accounts

    def select_accounts(self, account_id: str | None) -> list[dict]:
        accounts = self.accounts()
        if account_id:
            return [account for account in accounts if account["personal_account_id"] == account_id]
        if self.config.account_filter:
            selected = [
                account
                for account in accounts
                if any(
                    needle.lower()
                    in f"{account['id']} {account['personal_account_id']} {account['name']}".lower()
                    for needle in self.config.account_filter
                )
            ]
            if selected:
                return selected
        return accounts

    @property
    def default_account_id(self) -> str:
        if len(self.config.account_filter) == 1:
            matches = [
                account
                for account in self.accounts()
                if self.config.account_filter[0].lower()
                in f"{account['id']} {account['personal_account_id']} {account['name']}".lower()
            ]
            if len(matches) == 1:
                return matches[0]["personal_account_id"]
        return ""

    def receipt_pdf(self, month: str) -> bytes:
        with self.lock:
            self._ensure_login()
            accounts = self.select_accounts(None)
            if not accounts:
                raise MosOblEIRCError("Лицевой счёт не найден")

            cache = ReceiptCache(self.config.receipts_dir)
            for account in accounts:
                data = cache.load(account["personal_account_id"], month)
                if data:
                    return data

            last_error: MosOblEIRCError | None = None
            for account in accounts:
                try:
                    data = self.client.receipt_pdf(account["personal_account_id"], f"{month}-01")
                except MosOblEIRCError as error:
                    last_error = error
                    continue
                cache.save(account["personal_account_id"], month, data)
                return data

        message = f"Квитанция за {month} не найдена"
        if last_error:
            message = f"{message}: {last_error}"
        raise MosOblEIRCError(message)

    def data(
        self,
        *,
        account_id: str | None,
        months: int | None,
        value: str | None,
        force: bool = False,
    ) -> dict:
        months = months or self.config.months
        value = value or self.config.value
        if value not in VALUE_FIELDS:
            raise ValueError(f"Неизвестный показатель: {value}")
        months = max(1, min(months, 60))

        def log(account, *args):
            print(f"[web] {account['name']}:", *args, file=sys.stderr)

        with self.lock:
            self._ensure_login()
            accounts = self.select_accounts(account_id)
            if not accounts:
                raise MosOblEIRCError("Лицевой счёт не найден")
            end_month = self.config.end_month or previous_month()
            month_list = month_range(end_month, months)

            cache_key = (
                tuple(sorted(account["personal_account_id"] for account in accounts)),
                tuple(month_list),
                value,
            )
            if not force:
                cached_payload = self._data_cache.get(cache_key)
                if cached_payload is not None:
                    return {
                        **cached_payload,
                        "fetch": {"cacheHit": True, "downloaded": 0, "parsed": 0, "cached": 0},
                    }

            report: dict = {}
            receipt_rows: list = []
            receipts_error: str | None = None
            if not pdf_support_available():
                receipts_error = "Квитанции ЕПД не разобраны: установите зависимости — pip install -r requirements.txt"
                log(accounts[0], receipts_error)
            else:
                try:
                    receipt_rows = collect_receipt_charges(
                        self.client,
                        accounts,
                        month_list,
                        cache_dir=self.config.receipts_dir,
                        force=force,
                        log=lambda account, month, note: log(account, month, note),
                        report=report,
                    )
                except MosOblEIRCError as error:
                    log(accounts[0], f"квитанции недоступны: {error}")

            if receipt_rows:
                category_rows = receipt_rows
                category_source = "receipts"
            else:
                category_rows = collect_charges(
                    self.client,
                    accounts,
                    month_list,
                    anchor_day=self.config.anchor_day,
                    shift=self.config.shift,
                    cache_dir=self.config.cache_dir,
                    force=force,
                    log=lambda account, month, requested_date, count, source: log(
                        account, month, f"(запрос {requested_date}, {source}, услуг: {count})"
                    ),
                )
                category_source = "charge-details"

        months_cat, services, totals_cat, grand_total = aggregate(category_rows, value)
        history = has_category_history(category_rows)

        _, charged_services, _, _ = aggregate(category_rows, "charged")
        _, volume_services, _, _ = aggregate(category_rows, "volume")

        service_groups: dict[str, str] = {}
        for row in category_rows:
            service_groups.setdefault(row.service, row.group or "Услуги")
        groups: dict[str, list[str]] = {}
        for service in services:
            groups.setdefault(service_groups.get(service, "Услуги"), []).append(service)
        order = [service for names in groups.values() for service in names]

        if category_source == "charge-details" and not history and months_cat:
            latest = months_cat[-1]
            services = {
                service: {latest: by_month.get(latest, 0.0)}
                for service, by_month in services.items()
            }
            totals_cat = {latest: totals_cat.get(latest, 0.0)}
            grand_total = totals_cat[latest]
            months_cat = [latest]

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
                "value": value,
                "history": history,
                "source": category_source,
                "error": receipts_error,
            },
            "accounts": [
                {"name": account["name"], "personal_account_id": account["personal_account_id"]}
                for account in accounts
            ],
            "rows": len(category_rows),
            "fetch": {
                "cacheHit": False,
                "downloaded": report.get("downloaded", 0),
                "parsed": report.get("parsed", 0),
                "cached": report.get("cached", 0),
            },
        }

        with self.lock:
            if force or report.get("downloaded") or report.get("parsed"):
                self._data_cache.clear()
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

            if parsed.path == "/api/accounts":
                refresh = query.get("refresh", ["0"])[0] == "1"
                self._send_json(
                    {
                        "accounts": self.state.accounts(refresh=refresh),
                        "default": self.state.default_account_id,
                    }
                )
                return

            if parsed.path == "/api/data":
                months_value = query.get("months", [None])[0]
                self._send_json(
                    self.state.data(
                        account_id=query.get("account_id", [None])[0] or None,
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
                data = self.state.receipt_pdf(month)
                self._send_bytes(
                    data,
                    "application/pdf",
                    200,
                    {"Content-Disposition": f'inline; filename="{month}.pdf"'},
                )
                return

            self._send_json({"message": "not found"}, 404)
        except MosOblEIRCError as error:
            print(f"[web] ошибка: {error}", file=sys.stderr)
            self._send_json({"message": str(error)}, 502)
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

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Остановлено", file=sys.stderr)
    finally:
        server.server_close()
