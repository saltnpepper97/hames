// Playwright-side access to the shared page. Both candidates use this so that the
// "agent" half of the bidirectional proof is identical.

/**
 * @param {{ getPage: () => import("playwright").Page, getContext: () => import("playwright").BrowserContext, getCdp?: () => import("playwright").CDPSession | null }} options
 */
export function createPageTools(options) {
  const { getPage, getContext, getCdp } = options;

  return {
    page: () => getPage(),

    /** Fixture-provided state read straight from the document. */
    async fixtureState() {
      return await getPage().evaluate(() =>
        typeof window.__FIXTURE_STATE__ === "function" ? window.__FIXTURE_STATE__() : null,
      );
    },

    /** Page identity as Playwright and CDP see it. */
    async identity() {
      const page = getPage();
      const context = getContext();
      const cdp = getCdp?.() ?? null;
      let targetId = null;
      let pageTargets = null;
      if (cdp) {
        const targetInfo = await cdp.send("Target.getTargetInfo");
        targetId = targetInfo.targetInfo.targetId;
        const { targetInfos } = await cdp.send("Target.getTargets");
        pageTargets = targetInfos.filter((info) => info.type === "page").map((info) => ({
          targetId: info.targetId,
          url: info.url,
          title: info.title,
        }));
      }
      return {
        url: page.url(),
        title: await page.title(),
        closed: page.isClosed(),
        contextPageCount: context.pages().length,
        targetId,
        pageTargets,
      };
    },

    /** Combined state: identity, fixture counters, and the number of observers. */
    async snapshot() {
      const identity = await this.identity();
      const fixture = await this.fixtureState();
      return { ...identity, fixture };
    },

    async click(selector) {
      await getPage().click(selector, { timeout: 10000 });
    },

    /** Playwright typing, so the page receives real key events. */
    async type(selector, text) {
      await getPage().click(selector, { timeout: 10000 });
      await getPage().keyboard.type(text, { delay: 10 });
    },

    async fill(selector, value) {
      await getPage().fill(selector, value, { timeout: 10000 });
    },

    async press(key) {
      await getPage().keyboard.press(key);
    },

    /** Must be called after focusing the browser window; used by the virtual-display candidate. */
    async focusPage() {
      await getPage().bringToFront();
    },

    async evaluate(expression) {
      return await getPage().evaluate(expression);
    },

    async setMarker(text) {
      await getPage().evaluate((value) => {
        const node = document.getElementById("agent-marker");
        if (node) node.textContent = value;
      }, text);
    },

    async screenshot(path, options = {}) {
      await getPage().screenshot({ path, type: "png", ...options });
      return path;
    },

    async goto(url) {
      await getPage().goto(url, { waitUntil: "load", timeout: 30000 });
      await getPage().waitForFunction(() => typeof window.__FIXTURE_STATE__ === "function", null, {
        timeout: 10000,
      });
    },
  };
}
