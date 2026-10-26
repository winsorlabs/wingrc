import { chromium } from "playwright";
import fs from "fs";

const build = process.argv[2];
const host = `v1-web-${build}`;
const OUT = `/b/pw/out/${build}`;
fs.mkdirSync(OUT, { recursive: true });
const tok = fs.readFileSync("/b/session.txt", "utf8").trim();

const MEASURE = () => {
  const parse = (c) => {
    const m = c && c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const blend = (t, b) => [0, 1, 2].map((i) => t[i] * t[3] + b[i] * (1 - t[3])).concat(1);
  const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hex = (c) => "#" + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  const chainOf = (el) => { const a = []; for (let e = el; e; e = e.parentElement) a.push(e); return a.reverse(); };
  const bgOf = (el) => {
    let bg = [255, 255, 255, 1], grad = false;
    for (const e of chainOf(el)) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage !== "none") grad = true;
      const c = parse(cs.backgroundColor);
      if (c && c[3] > 0) bg = blend(c, bg);
    }
    return { bg, grad };
  };
  const opacityOf = (el) => chainOf(el).reduce((o, e) => o * Number(getComputedStyle(e).opacity), 1);
  const desc = (e) => e.tagName.toLowerCase() + (e.className && typeof e.className === "string" ? "." + e.className.trim().split(/\s+/).join(".") : "");
  const visible = (e) => {
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none";
  };
  const covered = (e) => {
    const r = e.getBoundingClientRect();
    const x = r.left + Math.min(r.width / 2, 6), y = r.top + r.height / 2;
    if (y < 0 || y > innerHeight || x < 0 || x > innerWidth) return null;
    const hit = document.elementFromPoint(x, y);
    return !(hit && (hit === e || e.contains(hit) || hit.contains(e)));
  };
  const text = [], nontext = [];
  for (const e of document.querySelectorAll("body *")) {
    if (!visible(e)) continue;
    const cs = getComputedStyle(e);
    const own = [...e.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(" ").trim();
    const inputLike = (e.tagName === "INPUT" || e.tagName === "TEXTAREA" || e.tagName === "SELECT");
    const cov = covered(e);
    if (cov === true) continue;
    const { bg, grad } = bgOf(e);
    if (own || (inputLike && e.value)) {
      const fg = parse(cs.color);
      const op = opacityOf(e);
      const eff = blend([fg[0], fg[1], fg[2], fg[3] * op], bg);
      text.push({ sel: desc(e), text: (own || e.value).slice(0, 50), fg: hex(fg), fgEff: hex(eff), bg: hex(bg), ratio: +ratio(eff, bg).toFixed(2),
        size: cs.fontSize, weight: cs.fontWeight, opacity: +op.toFixed(2), disabled: !!e.closest(":disabled"), grad, offscreen: cov === null });
    }
    if (inputLike && e.placeholder && !e.value) {
      const pc = parse(getComputedStyle(e, "::placeholder").color);
      text.push({ sel: desc(e) + "::placeholder", text: e.placeholder.slice(0, 50), fg: hex(pc), fgEff: hex(blend(pc, bg)), bg: hex(bg), ratio: +ratio(blend(pc, bg), bg).toFixed(2), size: cs.fontSize, weight: cs.fontWeight, opacity: 1, disabled: e.disabled, grad, offscreen: cov === null });
    }
    const parentBg = e.parentElement ? bgOf(e.parentElement).bg : bg;
    for (const prop of ["borderTopColor", "borderBottomColor", "borderLeftColor", "outlineColor", "backgroundColor", "fill", "stroke"]) {
      const c = parse(cs[prop]);
      if (!c || c[3] === 0) continue;
      if (prop.startsWith("border") && cs[prop.replace("Color", "Width")] === "0px") continue;
      if (prop === "outlineColor" && cs.outlineStyle === "none") continue;
      const h = hex(c);
      if (["#0a58ca", "#0846a2", "#0d6efd", "#0b5ed7"].includes(h)) {
        nontext.push({ sel: desc(e), prop, color: h, against: hex(prop === "backgroundColor" ? parentBg : bg), ratio: +ratio(c, prop === "backgroundColor" ? parentBg : bg).toFixed(2), againstParent: hex(parentBg), ratioParent: +ratio(c, parentBg).toFixed(2) });
      }
    }
  }
  return { text, nontext };
};

const HOVER_SEL = [".btn-primary", ".btn-ghost", ".link-button", ".breadcrumb a", ".filter-chip--active", ".product-deactivate-btn", ".ev-download", ".drawer-guidance-toggle", ".doc-body a"];

async function measureAll(page, name) {
  const seen = new Map();
  const steps = await page.evaluate(() => Math.ceil(document.documentElement.scrollHeight / innerHeight));
  for (let i = 0; i < Math.min(steps, 6); i++) {
    await page.evaluate((y) => window.scrollTo(0, y * innerHeight), i);
    await page.waitForTimeout(250);
    const r = await page.evaluate(MEASURE);
    for (const t of r.text) seen.set("t|" + t.sel + "|" + t.text + "|" + t.bg, t);
    for (const n of r.nontext) seen.set("n|" + n.sel + "|" + n.prop + "|" + n.against, { ...n, nontext: true });
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  const hovers = [];
  for (const sel of HOVER_SEL) {
    const els = page.locator(`${sel}:visible`);
    const n = Math.min(await els.count(), 3);
    for (let i = 0; i < n; i++) {
      const el = els.nth(i);
      try {
        if (await el.isDisabled().catch(() => false)) continue;
        await el.hover({ timeout: 2000 });
        await page.waitForTimeout(250);
        const h = await el.evaluate((e) => {
          const cs = getComputedStyle(e);
          return { sel: e.tagName.toLowerCase() + "." + String(e.className).trim().split(/\s+/).join("."), text: e.textContent.trim().slice(0, 40), color: cs.color, bg: cs.backgroundColor, decoration: cs.textDecorationLine };
        });
        hovers.push({ screen: name, ...h });
      } catch {}
    }
  }
  await page.mouse.move(1, 760);
  return { items: [...seen.values()], hovers };
}

async function focusWalk(page, name, n = 14) {
  await page.mouse.click(1, 400);
  const out = [];
  for (let i = 0; i < n; i++) {
    await page.keyboard.press("Tab");
    await page.waitForTimeout(120);
    const f = await page.evaluate(() => {
      const e = document.activeElement;
      if (!e || e === document.body) return null;
      const cs = getComputedStyle(e);
      return { sel: e.tagName.toLowerCase() + "." + String(e.className).trim().split(/\s+/).join("."), text: (e.textContent || e.getAttribute("aria-label") || e.placeholder || "").trim().slice(0, 30),
        outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth, outlineColor: cs.outlineColor, outlineOffset: cs.outlineOffset,
        borderColor: cs.borderTopColor, borderWidth: cs.borderTopWidth, boxShadow: cs.boxShadow, bg: cs.backgroundColor, focusVisible: e.matches(":focus-visible") };
    });
    if (f) out.push({ screen: name, ...f });
  }
  return out;
}

const browser = await chromium.launch();
const results = { screens: {}, hovers: [], focus: [] };

async function newPage(withCookie = true) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  if (withCookie) await ctx.addCookies([{ name: "wingrc_session", value: tok, domain: host, path: "/" }]);
  const page = await ctx.newPage();
  await page.goto(`http://${host}/`);
  await page.waitForTimeout(2000);
  return page;
}
const click = async (page, t, opts = {}) => { await (opts.in ? page.locator(opts.in) : page).getByText(t, { exact: true }).first().click(); await page.waitForTimeout(opts.wait ?? 1500); };
const closeDrawer = async (page) => { if (await page.locator(".drawer-close").count()) { await page.locator(".drawer-close").first().click(); await page.waitForTimeout(500); } };
const nav = async (page, top, sub) => { await closeDrawer(page); await click(page, top, { in: ".side-nav" }); if (sub) await click(page, sub, { in: ".side-nav" }); };
const intoOrg = async (page, org) => { await click(page, org); await click(page, "Open", { wait: 2500 }); };

async function shot(page, name, { focus = false } = {}) {
  await page.screenshot({ path: `${OUT}/${name}.png` });
  const m = await measureAll(page, name);
  results.screens[name] = m.items;
  results.hovers.push(...m.hovers);
  if (focus) results.focus.push(...(await focusWalk(page, name)));
  console.log(build, name, m.items.length);
}

const SCREENS = [
  ["01-org-picker", async (p) => {}, { focus: true }],
  ["02-dashboard", async (p) => { await intoOrg(p, "Acme MSP"); }],
  ["03-board", async (p) => { await intoOrg(p, "Acme MSP"); await nav(p, "Assessments", "Assessment Board"); await p.locator(".family-header").first().click(); await p.waitForTimeout(800); }],
  ["04-control-drawer", async (p) => { await intoOrg(p, "Acme MSP"); await nav(p, "Assessments", "Assessment Board"); await p.locator(".family-header").first().click(); await p.waitForTimeout(800); await p.locator(".edit-stmt-btn").first().click(); await p.waitForTimeout(2000); }, { focus: true }],
  ["05-tools", async (p) => { await intoOrg(p, "Acme MSP"); await nav(p, "Tools"); await p.waitForTimeout(1000); }, { focus: true }],
  ["06-lists-index", async (p) => { await intoOrg(p, "Winsorlabs"); await nav(p, "Library", "Lists"); }],
  ["07-list-page", async (p) => { await intoOrg(p, "Winsorlabs"); await nav(p, "Library", "Lists"); await p.locator(".link-button:visible").first().click(); await p.waitForTimeout(2000); }],
  ["08-documents", async (p) => { await intoOrg(p, "Winsorlabs"); await nav(p, "Library", "All Documents"); }],
  ["09-document", async (p) => { await intoOrg(p, "Winsorlabs"); await nav(p, "Library", "All Documents"); await click(p, "Access Control Policy", { wait: 2500 }); }, { focus: true }],
  ["10-refusal-panel", async (p) => { await intoOrg(p, "Winsorlabs"); await nav(p, "Library", "All Documents"); await click(p, "Access Control Policy", { wait: 2500 }); await p.locator('[aria-label="Remove AC.L2-3.1.1[a]"]').first().click(); await p.waitForTimeout(2000); await p.locator(".list-tag-confirm").scrollIntoViewIfNeeded(); }],
  ["11-assets", async (p) => { await intoOrg(p, "Acme MSP"); await nav(p, "Scope", "Assets"); }],
  ["12-org-profile", async (p) => { await intoOrg(p, "Acme MSP"); await nav(p, "Scope"); await p.locator(".side-nav-subitem", { hasText: "Org Profile" }).first().click(); await p.waitForTimeout(1500); }, { focus: true }],
  ["13-periodic-review", async (p) => { await intoOrg(p, "Acme MSP"); await nav(p, "Scope", "Periodic Review"); }],
  ["14-users", async (p) => { await intoOrg(p, "Acme MSP"); await nav(p, "Security", "Users"); }],
  ["15-audit-log", async (p) => { await intoOrg(p, "Acme MSP"); await nav(p, "Security", "Audit Log"); }],
  ["16-roles", async (p) => { await intoOrg(p, "Acme MSP"); await nav(p, "Assessments", "Roles"); }],
  ["17-admin", async (p) => { await p.locator("button", { hasText: "🛠" }).first().click(); await p.waitForTimeout(2000); }],
  ["20-admin-users", async (p) => { await p.locator("button", { hasText: "🛠" }).first().click(); await p.waitForTimeout(1500); await click(p, "Users", { in: ".side-nav" }); }],
  ["18-admin-integrations", async (p) => { await p.locator("button", { hasText: "🛠" }).first().click(); await p.waitForTimeout(1500); await click(p, "Integrations"); }],
];

for (const [name, go, opts] of SCREENS) {
  try {
    const page = await newPage();
    await go(page);
    await shot(page, name, opts);
    await page.context().close();
  } catch (e) { console.log(build, name, "FAILED", String(e).split("\n")[0]); }
}
try {
  const page = await newPage(false);
  await shot(page, "19-login", { focus: true });
  await page.context().close();
} catch (e) { console.log(build, "19-login FAILED", String(e).split("\n")[0]); }

fs.writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
await browser.close();
