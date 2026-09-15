from utils import init_browser, login_tiktok, get_all_friends
from dotenv import load_dotenv
import os

load_dotenv()

if __name__ == "__main__":
    browser, wait = init_browser()
    login_tiktok(browser, wait)
    get_all_friends(browser, wait)