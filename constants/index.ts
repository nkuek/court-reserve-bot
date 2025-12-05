import { Browser, Builder } from "selenium-webdriver";

export const base = "https://app.courtreserve.com";
export const driver = await new Builder().forBrowser(Browser.CHROME).build();
