import { chromium } from "playwright";
const browser = await chromium.launch();
for (const build of ["main","branch"]) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  await page.goto(`http://v1-web-${build}/`); await page.waitForTimeout(1500);
  await page.locator("input >> nth=0").fill("someone@example.com"); await page.locator("input >> nth=1").fill("x");
  const btn = page.locator("button.btn-primary").first();
  const box = await btn.boundingBox(); const clip = { x: box.x - 6, y: box.y - 6, width: 200, height: box.height + 12 };
  await page.mouse.move(5, 5); await page.waitForTimeout(300);
  await page.screenshot({ path: `/b/pw/rest-${build}.png`, clip });
  await btn.hover(); await page.waitForTimeout(400);
  await page.screenshot({ path: `/b/pw/hov-${build}.png`, clip });
  await ctx.close();
}
await browser.close();
