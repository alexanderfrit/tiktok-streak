from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time, re, csv, os
from dotenv import load_dotenv

load_dotenv()

def init_browser(headless=True):
    chrome_options = Options()
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    if headless:
        chrome_options.add_argument("--headless=new")
    browser = webdriver.Chrome(options=chrome_options)

    wait = WebDriverWait(browser, 20)

    return browser, wait


def login_tiktok(browser, wait, username=None, password=None):
    # ponytail: sessionid cookie injection; automated credentials login bypassed, add when headless re-auth flow needed.
    session_id = os.getenv("TIKTOK_SESSION_ID")
    if not session_id:
        raise ValueError("TIKTOK_SESSION_ID not found in .env")

    browser.get("https://www.tiktok.com")
    for cookie_name in ("sessionid", "sessionid_ss"):
        browser.add_cookie({
            "name": cookie_name,
            "value": session_id,
            "domain": ".tiktok.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        })

    browser.get("https://www.tiktok.com/messages?lang=vi")
    time.sleep(3)
    if "login" in browser.current_url:
        raise RuntimeError("Session invalid or expired. Check TIKTOK_SESSION_ID in .env.")
    print("Session authenticated")


def get_all_friends(browser, wait):
    browser.get('https://www.tiktok.com/messages?lang=vi')

    all_user = wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "css-2tydh5-PInfoNickname")))

    my_friends = []
    with open('friends.csv', mode='r', newline='') as file:
        reader = csv.DictReader(file)
        my_friends = [row['Username'] for row in reader]

    for user in all_user:
        user.click()
        time.sleep(2)
        profile_element = wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "css-1qxabns-StyledLink")))[0]
        href = profile_element.get_attribute("href")
        username = re.search(r"/@(.+)", href).group(1)

        with open('friends.csv', mode='a', newline='') as file:
            if username in my_friends:
                continue
            writer = csv.writer(file)
            if file.tell() == 0:
                writer.writerow(['Username'])
            writer.writerow([username])

    browser.quit()


def auto_send_message(browser, wait):
    browser.get('https://www.tiktok.com/messages?lang=vi')

    
    my_friends = []
    with open('friends.csv', mode='r', newline='') as file:
        reader = csv.DictReader(file)
        my_friends = [row['Username'] for row in reader]

    all_user = wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "css-1mez8np-PInfoNickname")))

    for user in all_user:
        user.click()
        time.sleep(2)
        profile_element = wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "css-1qxabns-StyledLink")))[0]
        href = profile_element.get_attribute("href")
        username = re.search(r"/@(.+)", href).group(1)

        if username not in my_friends:
            continue

        try: 
            print("Sending message to", username)
            message_input = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "public-DraftStyleDefault-block")))
            message_input.click()
            message_input.send_keys(os.getenv('MESSAGE'))
            message_input.send_keys(Keys.RETURN)

        except:
            print("Can't get user name")

