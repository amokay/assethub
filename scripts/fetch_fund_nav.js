#!/usr/bin/env node
/* AssetHub 美元基金官网净值抓取器：输出 JSON {isin: {name, nav, date}}
 * 源：贝莱德（TW 官网表格）、富兰克林（FT.lu 官网）；每只基金独立配置，官网结构变化只需改这里。
 * 用法: node fetch_fund_nav.js
 */
const puppeteer = require('puppeteer-core');
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36';

const SOURCES = [
  {
    isin: 'LU0109392836', name: '富兰克林科技',
    url: 'https://www.franklintempleton.lu/our-funds/price-and-performance/products/4916/Z/franklin-technology-fund/LU0109392836',
    // 页面直接含 "As of dd/mm/yyyy NAV $xx.xx"（NAV 后可能有脚注数字）
    parse: function(txt) {
      const m = txt.match(/As of\s+(\d{2})\/(\d{2})\/(\d{4})[\s\S]{0,200}?NAV[\s\S]{0,60}?\$([\d,]+\.\d{2})/);
      if (!m) return null;
      const nav = parseFloat(m[4].replace(/,/g, ''));
      const date = m[3] + '-' + m[2] + '-' + m[1];
      return { nav, date };
    }
  },
  // 贝莱德走 DNB（见 update_fund_nav.py，curl 稳定）；富达日本官网被 Akamai 封锁暂无自动源
];

async function renderText(p, url, clickSel) {
  // 官网偶发反爬/限流（BlackRock Akamai 对高频请求限流）：慢速重试最多 4 轮
  for (let attempt = 1; attempt <= 4; attempt++) {
    try {
      await p.goto(url, { waitUntil: attempt === 1 ? 'networkidle2' : 'domcontentloaded', timeout: 45000 });
      await new Promise(r => setTimeout(r, 5000));
      const txt = await p.evaluate(function() { return document.body ? document.body.innerText.replace(/\n+/g, ' | ') : ''; });
      if (txt.length > 2000) return txt;
      if (attempt === 2) await p.goto(url, { waitUntil: 'networkidle2', timeout: 45000 });
      await new Promise(r => setTimeout(r, 8000));   // 冷却，避开限流窗口
    } catch (e) {
      if (attempt === 4) throw e;
      await new Promise(r => setTimeout(r, 10000));
    }
  }
  return '';
}

(async () => {
  const b = await puppeteer.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: 'new', args: ['--no-sandbox']
  });
  const out = {};
  for (const s of SOURCES) {
    const p = await b.newPage();
    await p.setUserAgent(UA);
    try {
      const txt = await renderText(p, s.url, null);   // 贝莱德 TW 默认即显示完整份额表格，无需点击
      const r = s.parse(txt);
      if (r) { out[s.isin] = { name: s.name, nav: r.nav, date: r.date }; }
      else { out[s.isin] = { name: s.name, error: '解析失败', htmlLen: txt.length }; }    } catch (e) {
      out[s.isin] = { name: s.name, error: e.message.slice(0, 80) };
    }
    await p.close();
  }
  await b.close();
  console.log(JSON.stringify(out, null, 1));
})();
