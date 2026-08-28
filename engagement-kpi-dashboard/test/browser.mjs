/* One way to launch Chromium, wherever the tests happen to be running.
 *
 * The sandbox these were written in preinstalls Chromium at a fixed path, and
 * that path went into every harness. A CI runner has Playwright's own download
 * somewhere else entirely, so all six failed there with "executable doesn't
 * exist" — a bug that could only ever show up off the machine it was written on.
 *
 * Prefer the preinstalled binary when it is actually present; otherwise say
 * nothing and let Playwright resolve the browser it installed itself.
 */
import fs from 'fs';
import { chromium } from 'playwright';

const PREINSTALLED = '/opt/pw-browsers/chromium';

export const launch = (options = {}) => chromium.launch({
  ...(fs.existsSync(PREINSTALLED) ? { executablePath: PREINSTALLED } : {}),
  ...options,
});
