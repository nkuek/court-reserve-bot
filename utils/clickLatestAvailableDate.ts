import { By } from "selenium-webdriver";
import { find } from "./find";
import { base, driver } from "../constants";

// Dates are released 5 days in advance
export async function clickLatestAvailableDate() {
  await driver.get(`${base}/Online/Reservations/Bookings/8449?sId=18493`);
  await driver.sleep(1000);

  const datePicker = await find(By.css('a[data-testid="link-0"]'));
  await datePicker.click();

  const currentDate = new Date();
  const futureDate = new Date(currentDate);
  // Get the date 5 days from now
  futureDate.setDate(futureDate.getDate() + 5);

  const formattedDate = `${futureDate.getFullYear()}/${futureDate.getMonth()}/${futureDate.getDate()}`;

  const date = await find(By.css(`a[data-value="${formattedDate}"]`));
  await date.click();
}
