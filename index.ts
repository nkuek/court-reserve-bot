import {
  Browser,
  Builder,
  By,
  WebDriver,
  WebElement,
  until,
  error as SeleniumError,
  Key,
} from "selenium-webdriver";
import minimist from "minimist";

// ---------- CLI ARG: --time=HH:MM (24h) ----------

const argv = minimist(process.argv.slice(2));

const timeArg = argv.time as string | undefined;
const durationHoursArg = argv.duration as string | undefined;

if (!durationHoursArg) {
  throw new Error(
    "Missing --duration.\nUsage: node reserve.js --time=21:00 --duration=2",
  );
}

const durationHours = Number(durationHoursArg);
if (Number.isNaN(durationHours) || durationHours <= 0) {
  throw new Error(`Invalid --duration value: ${durationHoursArg}`);
}

if (!timeArg) {
  throw new Error(
    "Missing --time.\nUsage: node reserve.js --time=21:00  # 9:00 PM",
  );
}

// Parse time (24h)
const [hour, minute] = timeArg.split(":").map(Number);
if (hour === undefined || minute === undefined) {
  throw new Error(`Invalid --time format: ${timeArg}`);
}

if (
  Number.isNaN(hour) ||
  Number.isNaN(minute) ||
  hour < 0 ||
  hour > 23 ||
  minute < 0 ||
  minute > 59
) {
  throw new Error(`Invalid --time value: ${timeArg}`);
}

function to12Hour(time24: string | Date) {
  let d = new Date();
  if (typeof time24 === "string") {
    const [hour, minute] = time24.split(":").map(Number);
    if (hour === undefined || minute === undefined) {
      throw new Error(`Invalid time format: ${time24}`);
    }
    d.setHours(hour, minute, 0, 0);
  } else {
    d = time24;
  }

  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function find(
  driver: WebDriver,
  by: By,
  timeout = 3000,
): Promise<WebElement> {
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
// convert duration hours → dropdown index
function durationToIndex(hours: number): number {
  const options = [1, 1.5, 2, 2.5, 3];
  // 1 hour is selected by default, so subtract 1
  const index = options.indexOf(hours) - 1;
  if (index === -1) {
    throw new Error(
      `Unsupported duration: ${hours}. Must be 1, 1.5, 2, 2.5, or 3`,
    );
  }
  return index;
}

const base = "https://app.courtreserve.com";
// compute target time ONCE
const target = new Date();
target.setHours(hour, minute, 0, 0); // 21:00:00.000 (9:00 PM)

const RESERVATION_TIME = to12Hour(timeArg);
const start = new Date(target.getTime());

// Convert hours → milliseconds
const durationMs = durationHours * 60 * 60 * 1000;

// Subtract 30 minutes
const adjustedDurationMs = durationMs - 30 * 60 * 1000;

if (adjustedDurationMs < 0) {
  throw new Error("Duration must be at least 0.5 hours.");
}

// Compute end time
const endTime = new Date(start.getTime() + adjustedDurationMs);

const END_TIME = to12Hour(endTime);
console.log(RESERVATION_TIME, END_TIME);

const COURTS = [
  "Pickleball Court 5C (Bubble B)",
  "Pickleball Court 5B (Bubble B)",
  "Pickleball Court 5A (Bubble B)",
  "Pickleball Court 6A (Bubble B)",
  "Pickleball Court 6B (Bubble B)",
  "Pickleball Court 6C (Bubble B)",
  "Pickleball Court #7A (Bubble B)",
  "Pickleball Court #7B (Bubble B)",
  "Pickleball Court #8A (Bubble B)",
  "Pickleball Court #8B (Bubble B)",
];

const driver = await new Builder().forBrowser(Browser.CHROME).build();

async function login() {
  const email = process.env.EMAIL as string | undefined;
  const password = process.env.PASSWORD as string | undefined;
  console.log(email, password);
  if (email === undefined || password === undefined) {
    throw new Error("Missing EMAIL or PASSWORD environment variables.");
  }
  await driver.get(`${base}/Account/Login`);
  const form = await find(driver, By.css("form"));
  const inputs = await form.findElements({ tagName: "input" });

  await Promise.all([
    inputs[0]!.sendKeys(email),
    inputs[1]!.sendKeys(password),
  ]);
  await form.submit();
  await driver.sleep(1000);
}

// Dates are released 5 days in advance
async function clickLatestAvailableDate() {
  await driver.get(`${base}/Online/Reservations/Bookings/8449?sId=18493`);
  await driver.sleep(1000);

  const datePicker = await find(driver, By.css('a[data-testid="link-0"]'));
  await datePicker.click();

  const currentDate = new Date();
  const futureDate = new Date(currentDate);
  // Get the date 5 days from now
  futureDate.setDate(futureDate.getDate() + 5);

  // NOTE: months are 0-based, so add 1 if the site expects human months
  const formattedDate = `${futureDate.getFullYear()}/${futureDate.getMonth()}/${futureDate.getDate()}`;

  const date = await find(driver, By.css(`a[data-value="${formattedDate}"]`));
  await date.click();
}

async function addPlayers() {
  console.log("Attempting to add placeholders...");
  // add players (3 times)
  for (let i = 0; i < 3; i++) {
    const additionalPlayersInput = await find(
      driver,
      By.name("OwnersDropdown_input"),
    );

    await additionalPlayersInput.sendKeys("Placeholder");

    // Wait for results to appear
    await find(driver, By.css("#OwnersDropdown_listbox li"), 5000);
    await driver.sleep(1000);

    // Highlight first option and select it via keyboard (kendo-friendly)
    await additionalPlayersInput.sendKeys(Key.ARROW_DOWN);
    await additionalPlayersInput.sendKeys(Key.ENTER);
  }
  console.log("Successfully added placeholders!");
}

async function clickDisclosure() {
  console.log("Attempting to click disclosure...");
  const disclosureLabel = await find(
    driver,
    By.css("label[for='DisclosureAgree']"),
  );
  await disclosureLabel.click();
  console.log("Successfully clicked disclosure!");
  await driver.sleep(1000);
}

async function addDuration() {
  console.log(`Attempting to set duration to ${durationHours} hours...`);
  const durationInput = await find(
    driver,
    By.css("span[aria-owns='Duration_listbox']"),
  );
  await durationInput.click();

  // Wait for dropdown to open
  await find(
    driver,
    By.css('ul[data-testid="Duration-container"][aria-hidden="false"]'),
    5000,
  );

  const durationIndex = durationToIndex(durationHours);
  console.log(
    `Selecting duration: ${durationHours} hours (index ${durationIndex})`,
  );
  for (let i = 0; i <= durationIndex; i++) {
    await durationInput.sendKeys(Key.ARROW_DOWN);
  }
  await durationInput.sendKeys(Key.ENTER);
  console.log("Successfully set duration!");
}

async function clickSaveButton() {
  console.log('Attempting to click "Save" button at target time...');
  const saveButton = await find(driver, By.css('button[data-testid="Save"]'));

  let delay = target.getTime() - Date.now();
  if (delay < 0) {
    delay = 0;
    console.log('Target time already passed, clicking "Save" immediately.');
  } else {
    console.log(`Waiting ${delay}ms until ${timeArg} before clicking Save...`);
  }

  await sleep(delay);

  await saveButton.click();
  console.log('Clicked "Save" button!');
  await driver.sleep(1000);
}

async function checkCourtAvailability(court: string) {
  console.log(`Trying court: ${court}`);
  try {
    const startTime = await find(
      driver,
      By.xpath(
        `//button[@data-courtlabel='${court}' and contains(text(), '${RESERVATION_TIME}')]`,
      ),
    );
    console.log(`Found time slot for court "${court}" at ${RESERVATION_TIME}`);
    // verify the time slot is available by checking the end time
    await find(
      driver,
      By.xpath(
        `//button[@data-courtlabel='${court}' and contains(text(), '${END_TIME}')]`,
      ),
    );
    console.log(`End time ${END_TIME} also available for court "${court}"`);
    await startTime.click();
  } catch (err) {
    throw Error(
      `Time slot for court "${court}" at ${RESERVATION_TIME} not found. It may be already booked. Trying next court...`,
    );
  }
}

await login();
await clickLatestAvailableDate();

for (const court of COURTS) {
  try {
    try {
      await checkCourtAvailability(court);
    } catch (err) {
      console.log(err);
      continue; // try next court
    }

    await addPlayers();
    await clickDisclosure();
    await addDuration();

    await clickSaveButton();

    const alerts = await driver.findElements(By.className("swal2-modal"));

    if (alerts.length > 0) {
      console.log(
        `SweetAlert detected after saving on court "${court}". Assuming failure/conflict, confirming and continuing...`,
      );

      // click the confirm button on the SweetAlert
      const confirmButton = await find(driver, By.css("button.swal2-confirm"));
      await confirmButton.click();
      const closeButton = await find(
        driver,
        By.css('button[data-testid="Close"]'),
      );
      await closeButton.click();

      // continue to next court (DO NOT break)
      continue;
    }
    console.log(`Successfully saved reservation on court: ${court}`);

    // If we got here without throwing, we consider it a success and stop
    break;
  } catch (err) {
    console.error(`Failed on court "${court}":`, err);
    // continue to next court
  }
}
