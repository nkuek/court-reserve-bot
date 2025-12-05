import {
  until,
  type By,
  type WebDriver,
  type WebElement,
  error as SeleniumError,
} from "selenium-webdriver";
import { driver } from "../constants";

export async function find(by: By, timeout = 3000): Promise<WebElement> {
  const end = Date.now() + timeout;

  while (true) {
    try {
      // Wait until *something* matching the locator exists
      await driver.wait(
        until.elementLocated(by),
        Math.max(0, end - Date.now()),
      );

      // Always get a fresh element instance
      const el = await driver.findElement(by);

      // Wait until that fresh element is visible
      await driver.wait(
        until.elementIsVisible(el),
        Math.max(0, end - Date.now()),
      );

      return el;
    } catch (err) {
      // If it's stale and we still have time, retry with a fresh find
      if (
        err instanceof SeleniumError.StaleElementReferenceError &&
        Date.now() < end
      ) {
        continue;
      }

      throw err;
    }
  }
}
