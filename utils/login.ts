import { By, type WebDriver } from "selenium-webdriver";
import { find } from "./find";
import { base, driver } from "../constants";

export async function login() {
  const email = process.env.EMAIL as string | undefined;
  const password = process.env.PASSWORD as string | undefined;
  if (email === undefined || password === undefined) {
    throw new Error("Missing EMAIL or PASSWORD environment variables.");
  }
  await driver.get(`${base}/Account/Login`);
  const form = await find(By.css("form"));
  const inputs = await form.findElements({ tagName: "input" });

  await Promise.all([
    inputs[0]!.sendKeys(email),
    inputs[1]!.sendKeys(password),
  ]);
  await form.submit();
  await driver.sleep(1000);
}
