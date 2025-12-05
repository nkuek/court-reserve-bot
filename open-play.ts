import { By } from "selenium-webdriver";
import dotenv from "dotenv";
import path from "path";
import { fileURLToPath } from "url";
import { find } from "./utils/find";
import { login } from "./utils/login";
import { clickLatestAvailableDate } from "./utils/clickLatestAvailableDate";
import { driver } from "./constants";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load .env *next to this file*
dotenv.config({ path: path.join(__dirname, ".env") });

await login();
await clickLatestAvailableDate();
console.log('Selecting "Pickleball Open Play - Intermediate" event details...');
const detailsLink = await find(
  By.xpath(
    "//span[@data-testid='reservation-name' and contains(., 'Pickleball Open Play - Intermediate')]" +
      "/ancestor::div[contains(@class,'reservation-container')]" +
      "//a[@data-testid='event-btn-detail' and normalize-space(.)='Details']",
  ),
);
await detailsLink.click();

console.log('Clicking "Register" link...');
const registerLink = await find(By.linkText("Register"));
await registerLink.click();

console.log("Finalizing registration...");
const finalizeButton = await find(
  By.xpath("//button[normalize-space(.)='Finalize Registration']"),
);
await finalizeButton.click();
console.log("Registration completed!");
await driver.quit();
