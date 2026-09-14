from utils import init_browser

if __name__ == "__main__":
    print("Launching browser for one-time login...")
    browser, _ = init_browser(headless=False)
    browser.get("https://www.tiktok.com/login")
    input("Log in manually, solve any verification, then press Enter here to save session: ")
    browser.quit()
    print("Session saved in chrome_profile/")
