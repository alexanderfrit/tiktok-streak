from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time, re, csv, os
from dotenv import load_dotenv

load_dotenv()

PROFILE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "chrome_profile"))


def init_browser(headless=True):
    chrome_options = Options()
    chrome_options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    chrome_options.add_argument("--disable-notifications")
    if headless:
        chrome_options.add_argument("--headless=new")
    browser = webdriver.Chrome(options=chrome_options)

    wait = WebDriverWait(browser, 20)

    return browser, wait


def login_tiktok(browser, wait, username=None, password=None):
    # ponytail: session persistence via chrome profile; automated login bypassed, add when headless re-auth flow needed.
    browser.get('https://www.tiktok.com/messages?lang=vi')
    time.sleep(3)
    if 'login' in browser.current_url:
        raise RuntimeError("Session not authenticated. Run setup_session.py to log in once.")
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

