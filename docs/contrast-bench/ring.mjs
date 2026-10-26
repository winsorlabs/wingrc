import { chromium } from "playwright";
import fs from "fs";
const browser = await chromium.launch();
for (const build of ["main","branch"]) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  await page.goto(`http://v1-web-${build}/`); await page.waitForTimeout(1500);
  await page.fill("input[type=email], input[name=email], input >> nth=0", "someone@example.com");
  await page.locator("input >> nth=1").fill("x");
  const btn = page.locator("button.btn-primary").first();
  await btn.focus(); await page.keyboard.press("Shift+Tab"); await page.keyboard.press("Tab");
  await page.waitForTimeout(300);
  const box = await btn.boundingBox();
  const clip = { x: box.x - 8, y: box.y - 8, width: Math.min(box.width + 16, 120), height: box.height + 16 };
  await page.screenshot({ path: `/b/pw/ring-${build}.png`, clip }); await btn.hover(); await page.waitForTimeout(300); await page.screenshot({ path: `/b/pw/ringhover-${build}.png`, clip });
  console.log(build, JSON.stringify(box), await btn.evaluate(e => e.matches(":focus-visible")));
  await ctx.close();
}
await browser.close();
